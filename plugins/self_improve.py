"""
self_improve — JARVIS writes new plugins for itself.

  "Learn how to check bitcoin prices"  ->  create
  "Make the weather plugin also say humidity" -> improve (own plugins only)

How it stays safe:
  1. The code is written to plugins/_pending/ — a folder the plugin loader never
     reads — so nothing runs just because it was generated.
  2. It is checked WITHOUT being run: it must parse, define PLUGIN and run(), and
     contain only imports/constants/functions at the top level. Risky calls
     (subprocess, deleting files, eval, sockets…) are listed for you to see.
  3. It is test-imported in a separate, time-limited process — top-level code
     only, run() is not called.
  4. The file opens on screen and installation WAITS for the CONFIRM button,
     which the model cannot press. Read it first; if you don't understand it,
     press cancel.
  5. Installed plugins can be removed again ("remove" moves them to
     plugins/_removed/, nothing is deleted). A restart activates new ones.

Only plugins created here can be improved or removed here; the bundled ones
are never touched.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from plugins._common import base_dir, ask_text, log, confirm, open_file, notify

PLUGIN = {
    "name": "self_improve",
    "description": (
        "Lets JARVIS write itself a NEW plugin (a new skill/tool) from a description, or "
        "improve a plugin it wrote earlier. Actions: create (description), improve "
        "(name + change), install (name, after the user has reviewed the code), list, "
        "remove (name). Installing needs the user to press CONFIRM on screen and a "
        "restart to activate. Use when asked to 'learn', 'add a skill for', 'teach "
        "yourself', or when no existing tool can do what the user wants."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "create | improve | install | list | remove"},
            "description": {"type": "STRING", "description": "For create: what the new skill should do, with examples of how the user will ask for it"},
            "name": {"type": "STRING", "description": "Plugin name (snake_case) for improve / install / remove"},
            "change": {"type": "STRING", "description": "For improve: what to change"},
        },
        "required": ["action"],
    },
    "behavior": "NON_BLOCKING",
}

_PLUGINS = base_dir() / "plugins"
_PENDING = _PLUGINS / "_pending"
_REMOVED = _PLUGINS / "_removed"
_REGISTRY = _PLUGINS / "_self_made.json"          # names of plugins this module installed
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

_RISKY_CALLS = {
    "os.system": "runs shell commands", "subprocess": "runs programs", "eval": "runs arbitrary text as code",
    "exec": "runs arbitrary text as code", "shutil.rmtree": "deletes folders", "os.remove": "deletes files",
    "os.unlink": "deletes files", "socket": "opens raw network connections", "ctypes": "low-level system access",
    "__import__": "dynamic imports", "pickle": "unsafe deserialisation", "winreg": "edits the Windows registry",
}

_RULES = """You write plugins for a voice assistant called JARVIS. Output ONLY the complete Python source of one plugin file — no markdown fences, no commentary.

HARD RULES
- The file must define a dict PLUGIN = {"name": <snake_case, 3-40 chars>, "description": <when the model should call it, with trigger phrases>, "parameters": {"type": "OBJECT", "properties": {...}, "required": [...]}} and a function run(parameters: dict, player=None, session_memory=None) -> str.
- Top level may contain ONLY: a docstring, imports, constants, the PLUGIN dict, and function/class definitions. Never run anything at import time.
- Import heavy or optional packages INSIDE run() or helpers, wrapped in try/except ImportError, returning a spoken sentence like "Sir, I need the 'x' package: pip install x".
- run() must NEVER raise: wrap in try/except and return a short natural-language string (it is spoken aloud). Keep results short.
- Use only the standard library plus: requests, beautifulsoup4, psutil, pillow, numpy, opencv-python, pyautogui, pyperclip. For free public web APIs that need no key, prefer them. Never invent API keys; if a key is needed read it from memory.config_manager.get_plugin_setting("<plugin name>", "api_key") and declare PLUGIN_SETTINGS = {"namespace": "<plugin name>", "title": "...", "fields": [{"key": "api_key", "label": "...", "type": "password"}]}.
- NEVER delete or overwrite the user's existing files, run shell commands, or send/post anything to anyone. If the task needs an irreversible action, import confirm from plugins._common and use confirm(key, title, detail, callable).
- Network calls: always pass timeout=15.
- Optional: log progress with `from plugins._common import log` then log(player, "JARVIS: ...").
- For long tasks add "behavior": "NON_BLOCKING" inside PLUGIN.

REFERENCE TEMPLATE
"""


def _registry() -> list[str]:
    try:
        return list(json.loads(_REGISTRY.read_text(encoding="utf-8")))
    except Exception:
        return []


def _save_registry(names: list[str]) -> None:
    _REGISTRY.write_text(json.dumps(sorted(set(names))), encoding="utf-8")


def _strip_fences(code: str) -> str:
    code = code.strip()
    code = re.sub(r"^```(?:python)?\s*", "", code)
    return re.sub(r"\s*```$", "", code).strip() + "\n"


def static_check(code: str) -> tuple[str, str, list[str]]:
    """Return (name, error, risks). Never executes the code."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return "", f"syntax error on line {e.lineno}: {e.msg}", []

    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef,
               ast.AsyncFunctionDef, ast.ClassDef, ast.Expr)
    has_run, name = False, ""
    for node in tree.body:
        if not isinstance(node, allowed):
            return "", f"top-level {type(node).__name__} is not allowed (only imports, constants and functions)", []
        if isinstance(node, ast.Expr) and not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            return "", "top-level code must not run anything at import time", []
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLUGIN" for t in node.targets):
            try:
                meta = ast.literal_eval(node.value)
                name = str(meta.get("name", ""))
            except Exception:
                return "", "PLUGIN must be a plain dict literal", []
    if not has_run:
        return "", "no run() function", []
    if not _NAME_RE.match(name):
        return "", f"PLUGIN name '{name}' must be snake_case, 3-40 chars", []

    risks: list[str] = []
    for node in ast.walk(tree):
        label = None
        if isinstance(node, ast.Call):
            f = node.func
            label = (f.id if isinstance(f, ast.Name) else
                     (f"{getattr(f.value, 'id', '')}.{f.attr}" if isinstance(f, ast.Attribute) else None))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                if m.split(".")[0] in _RISKY_CALLS:
                    label = m.split(".")[0]
        if label:
            for key, why in _RISKY_CALLS.items():
                if label == key or label.startswith(key + "."):
                    risks.append(f"{key} — {why}")
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
            if any(isinstance(a, ast.Constant) and isinstance(a.value, str) and set(a.value) & set("wax+")
                   for a in node.args[1:2]):
                risks.append("open(... write mode) — writes files")
    return name, "", sorted(set(risks))


def _import_test(path: Path) -> str:
    """Import the file in a separate, time-limited interpreter. Returns '' if fine."""
    code = (
        "import sys, importlib.util\n"
        f"sys.path.insert(0, {str(base_dir())!r})\n"
        f"spec = importlib.util.spec_from_file_location('plugins._probe', {str(path)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "from core.plugin_loader import _validate\n"
        "r = _validate(m, 'probe.py')\n"
        "print('OK' if r.valid else 'BAD: ' + r.error)\n"
    )
    try:
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                             timeout=25, cwd=str(base_dir()))
    except subprocess.TimeoutExpired:
        return "importing it took too long"
    last = (out.stdout.strip().splitlines() or [""])[-1]
    if last == "OK":
        return ""
    return last[4:] if last.startswith("BAD:") else (out.stderr.strip().splitlines() or ["import failed"])[-1][:200]


def _generate(task: str, existing: str = "") -> str:
    template = (_PLUGINS / "_template.py").read_text(encoding="utf-8")
    prompt = _RULES + template + "\n\n"
    if existing:
        prompt += f"EXISTING PLUGIN TO MODIFY (keep its name and everything not mentioned):\n{existing}\n\nCHANGE REQUESTED: {task}\n"
    else:
        prompt += f"WRITE A NEW PLUGIN FOR THIS REQUEST: {task}\n"
    from core import gemini
    return _strip_fences(gemini.text(prompt, gemini.SMART, None, 90_000) or "")


def _produce(task: str, existing: str, player) -> str:
    """Generate -> check -> import-test, retrying once with the error. Returns a spoken result."""
    last_err = ""
    for attempt in (1, 2):
        log(player, f"JARVIS: Writing the plugin (attempt {attempt})…")
        code = _generate(task if not last_err else f"{task}\n\nYOUR PREVIOUS ATTEMPT FAILED WITH: {last_err}. Fix it.", existing)
        if not code.strip():
            last_err = "the model returned nothing"
            continue
        name, err, risks = static_check(code)
        if err:
            last_err = err
            continue
        if existing and name != _extract_name(existing):
            last_err = "the plugin name must not change when improving"
            continue
        _PENDING.mkdir(parents=True, exist_ok=True)
        path = _PENDING / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        err = _import_test(path)
        if err:
            last_err = err
            path.unlink(missing_ok=True)
            continue
        (_PENDING / f"{name}.risks.json").write_text(json.dumps(risks), encoding="utf-8")
        open_file(path)
        notify("New plugin ready for review", name)
        risk_txt = ("It does: " + "; ".join(risks) + ". ") if risks else "It uses no risky calls. "
        return (f"I wrote a plugin called '{name}' and opened the code on screen for you to read. "
                f"{risk_txt}It passed the safety checks and a test import. "
                f"Say 'install {name}' if you want it — you'll then press CONFIRM and restart me.")
    return f"Sir, I couldn't produce a working plugin: {last_err}"


def _extract_name(code: str) -> str:
    return static_check(code)[0]


def _install(name: str, player) -> str:
    src = _PENDING / f"{name}.py"
    if not src.is_file():
        return f"There's no pending plugin called '{name}'. Ask me to create it first."
    code = src.read_text(encoding="utf-8")
    nm, err, risks = static_check(code)          # re-check: the file may have been edited by hand
    if err:
        return f"The pending file no longer passes the checks: {err}"
    dest = _PLUGINS / f"{nm}.py"
    mine = nm in _registry()
    if dest.exists() and not mine:
        return f"A bundled plugin named '{nm}' already exists and I won't overwrite it."

    def _do() -> str:
        try:
            if dest.exists():                       # improving one of mine: keep a backup
                _REMOVED.mkdir(exist_ok=True)
                shutil.copy2(dest, _REMOVED / f"{nm}.{time.strftime('%Y%m%d_%H%M%S')}.bak.py")
            shutil.move(str(src), str(dest))
            (_PENDING / f"{nm}.risks.json").unlink(missing_ok=True)
            _save_registry(_registry() + [nm])
            log(player, f"SYS: Plugin '{nm}' installed — restart JARVIS to activate it.")
            return f"Plugin '{nm}' installed. Restart me to activate it."
        except Exception as e:
            return f"Install failed: {e}"

    detail = f"{nm}.py ({len(code.splitlines())} lines)\n" + ("Risky: " + "; ".join(risks) if risks else "No risky calls detected")
    return confirm("self_improve_install", f"Install new plugin '{nm}'", detail, _do)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    name = (parameters.get("name") or "").strip().lower().replace(" ", "_")
    try:
        if action == "create":
            desc = (parameters.get("description") or "").strip()
            if len(desc) < 8:
                return "Describe the skill you want, with an example of how you'd ask for it."
            return _produce(desc, "", player)

        if action == "improve":
            change = (parameters.get("change") or "").strip()
            target = _PLUGINS / f"{name}.py"
            if name not in _registry() or not target.is_file():
                return "I can only improve plugins I wrote myself. Use 'list' to see them."
            if not change:
                return "What should I change?"
            return _produce(change, target.read_text(encoding="utf-8"), player)

        if action == "install":
            return _install(name, player)

        if action == "list":
            pending = sorted(p.stem for p in _PENDING.glob("*.py")) if _PENDING.exists() else []
            return (f"Installed (self-made): {', '.join(_registry()) or 'none'}. "
                    f"Waiting for your review: {', '.join(pending) or 'none'}.")

        if action == "remove":
            if name not in _registry():
                return "I can only remove plugins I wrote myself."
            src = _PLUGINS / f"{name}.py"

            def _do() -> str:
                _REMOVED.mkdir(exist_ok=True)
                if src.exists():
                    shutil.move(str(src), str(_REMOVED / f"{name}.py"))
                _save_registry([n for n in _registry() if n != name])
                return f"Plugin '{name}' removed (kept in plugins/_removed). Restart to finish."
            return confirm("self_improve_remove", f"Remove plugin '{name}'", "It is moved to plugins/_removed, not deleted.", _do)

        return "Unknown action. Use create, improve, install, list or remove."
    except Exception as e:
        return f"Sir, self-improvement failed: {e}"
