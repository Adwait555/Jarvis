"""
screen_agent — finish a multi-step task on the computer by looking at the screen.

The loop: take a screenshot -> ask Gemini for the single next action -> do it
with the mouse/keyboard -> look again, until the task is done or the step
limit is reached. It works in ANY app, because it works from pixels.

SAFETY
  * Hard cap on steps (default 15, max 40) and a per-step pause.
  * Slam the mouse into a screen CORNER at any time to abort (pyautogui failsafe).
  * Say "stop" -> the 'stop' action ends a running task after its current step.
  * The model is told never to type passwords, card numbers or one-time codes,
    never to confirm purchases, deletions or sending of messages, and to stop and
    report instead. Those remain yours to do.
For one-off single clicks computer_control is faster; this is for tasks that
need several steps and decisions.
"""
from __future__ import annotations

import io
import threading
import time

from plugins._common import log, ask_json
from memory.config_manager import get_plugin_setting

PLUGIN = {
    "name": "screen_agent",
    "description": (
        "Carries out a multi-step task on the computer by repeatedly looking at the "
        "screen and clicking/typing, in any application (e.g. 'in Photoshop export "
        "this as PNG', 'fill in this form with my details', 'change that setting'). "
        "Actions: run (task) or stop. For a single click/keypress use computer_control."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "run (default) | stop"},
            "task": {"type": "STRING", "description": "What to accomplish, in plain words"},
            "max_steps": {"type": "INTEGER", "description": "Step limit (default 15, max 40)"},
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
}

PLUGIN_SETTINGS = {
    "namespace": "screen_agent",
    "title": "Screen agent",
    "fields": [
        {"key": "step_delay", "label": "Seconds to wait after each action", "type": "text", "default": "1.2"},
        {"key": "model", "label": "Vision model (blank = auto ladder)", "type": "text", "default": ""},
    ],
}

_STOP = threading.Event()
_RUNNING = threading.Lock()

_PROMPT = """You are operating a computer to accomplish this task:
TASK: {task}

You see the current screen (width {w}, height {h} pixels). Steps done so far:
{history}

Decide the ONE next action. Reply with ONLY JSON:
{{"action": "click|double_click|right_click|type|key|scroll|wait|done|fail",
  "x": <pixel x, for click actions>, "y": <pixel y>,
  "text": "<for type>", "keys": "<for key, e.g. ctrl+s or enter or ctrl+shift+t>",
  "amount": <for scroll: positive up, negative down, in clicks>,
  "reason": "<max 12 words>"}}

Rules:
- Click the CENTER of the target element, using pixel coordinates of this screenshot.
- To enter text into a field, click it first (one step), then type (next step).
- Use "done" as soon as the task is visibly complete. Use "fail" if it cannot be done.
- NEVER type passwords, card numbers or one-time codes. NEVER confirm a purchase,
  payment, account deletion, or the sending/posting of anything. If the task needs
  that, use "fail" with the reason "needs the user to do this step".
- If nothing changed after your last action, try something different.
"""


def _shot(max_w: int = 1280):
    import mss
    from PIL import Image
    with mss.mss() as sct:
        mon = sct.monitors[1]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    full_w, full_h = img.size
    if full_w > max_w:
        img = img.resize((max_w, int(full_h * max_w / full_w)))
    return img, (full_w, full_h)


def _ask(img, task: str, history: list[str]) -> dict | None:
    from google.genai import types
    from core import gemini
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    prompt = _PROMPT.format(task=task, w=img.size[0], h=img.size[1],
                            history="\n".join(f"{i+1}. {h}" for i, h in enumerate(history[-8:])) or "(none yet)")
    model = str(get_plugin_setting("screen_agent", "model", "") or "").strip()
    parts = [types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"), prompt]
    raw = gemini.text(parts, model or gemini.SMART, None, 40_000)
    import json, re
    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return None


def _do(step: dict, scale: float) -> str:
    import pyautogui
    pyautogui.FAILSAFE = True
    act = str(step.get("action", "")).lower()
    try:
        x, y = int(float(step.get("x", 0)) * scale), int(float(step.get("y", 0)) * scale)
    except Exception:
        x = y = 0
    if act in ("click", "double_click", "right_click"):
        pyautogui.moveTo(x, y, duration=0.25)
        {"click": pyautogui.click, "double_click": pyautogui.doubleClick,
         "right_click": pyautogui.rightClick}[act]()
        return f"{act} ({x},{y})"
    if act == "type":
        text = str(step.get("text", ""))
        try:
            import pyperclip
            old = pyperclip.paste()
            pyperclip.copy(text)
            pyautogui.hotkey("command" if _is_mac() else "ctrl", "v")
            time.sleep(0.2)
            pyperclip.copy(old)
        except Exception:
            pyautogui.write(text, interval=0.02)
        return f"typed {len(text)} chars"
    if act == "key":
        keys = [k.strip() for k in str(step.get("keys", "")).lower().replace("+", " ").split() if k.strip()]
        if keys:
            pyautogui.hotkey(*keys)
        return f"key {'+'.join(keys)}"
    if act == "scroll":
        pyautogui.scroll(int(step.get("amount", -5)) * 100)
        return "scroll"
    if act == "wait":
        time.sleep(2)
        return "waited"
    return act


def _is_mac() -> bool:
    import platform
    return platform.system() == "Darwin"


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "run").strip().lower()
    if action == "stop":
        _STOP.set()
        return "Stopping after the current step."
    task = (parameters.get("task") or "").strip()
    if not task:
        return "Sir, what should I do on the screen?"
    if not _RUNNING.acquire(blocking=False):
        return "I'm already working on a screen task. Say 'stop' first."
    _STOP.clear()
    try:
        try:
            max_steps = max(1, min(40, int(parameters.get("max_steps") or 15)))
        except Exception:
            max_steps = 15
        try:
            delay = float(get_plugin_setting("screen_agent", "step_delay", "1.2") or 1.2)
        except Exception:
            delay = 1.2
        history: list[str] = []
        for n in range(1, max_steps + 1):
            if _STOP.is_set():
                return "Stopped as asked. " + (history[-1] if history else "")
            img, (fw, _fh) = _shot()
            scale = fw / img.size[0]
            step = _ask(img, task, history)
            if not step:
                history.append("(model gave no usable answer)")
                continue
            act = str(step.get("action", "")).lower()
            reason = str(step.get("reason", ""))
            log(player, f"JARVIS: [{n}/{max_steps}] {act} — {reason}")
            if act == "done":
                return f"Done: {reason or task}. Finished in {n} step(s)."
            if act == "fail":
                return f"I couldn't finish: {reason}."
            try:
                desc = _do(step, scale)
            except Exception as e:
                if "FailSafe" in type(e).__name__:
                    return "Aborted — the mouse hit the screen corner."
                desc = f"{act} failed: {e}"
            history.append(f"{desc} ({reason})")
            time.sleep(delay)
        return f"I reached the {max_steps}-step limit without finishing. Last: {history[-1] if history else 'nothing'}."
    except Exception as e:
        return f"Sir, the screen agent failed: {e}"
    finally:
        _RUNNING.release()
