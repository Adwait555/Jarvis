"""
Shared helpers for the creator / Google / phone plugins.

Underscore-prefixed, so the plugin loader never treats it as a plugin itself.
If you copy one of the plugins elsewhere, copy this file next to it.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def output_dir(kind: str) -> Path:
    """~/Documents/JARVIS Creations/<kind> (falls back to <app>/creations/<kind>)."""
    for root in (Path.home() / "Documents" / "JARVIS Creations",
                 base_dir() / "creations"):
        try:
            d = root / kind
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:
            continue
    return base_dir()


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def slug(text: str, n: int = 40) -> str:
    out = "".join(c if c.isalnum() else "_" for c in (text or "").lower()).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return (out[:n] or "item")


def open_file(path: str | Path) -> None:
    """Open a file in the OS default viewer. Never raises."""
    try:
        p = str(path)
        if platform.system() == "Windows":
            os.startfile(p)                                   # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


def log(player, text: str) -> None:
    if player:
        try:
            player.write_log(text)
        except Exception:
            pass


def gemini_client(timeout_ms: int = 120_000):
    from core import gemini
    return gemini.client(timeout_ms=timeout_ms)


def ask_text(prompt: str, tier=None, timeout_ms: int = 30_000) -> str:
    """One-shot text call through the model ladder."""
    from core import gemini
    return gemini.text(prompt, tier or gemini.FAST, None, timeout_ms)


def ask_json(prompt: str, contents=None, timeout_ms: int = 30_000):
    """One-shot call expected to return JSON. Returns parsed object or None."""
    import json, re
    from core import gemini
    try:
        raw = gemini.text(contents if contents is not None else prompt,
                          gemini.SMART, None, timeout_ms)
    except Exception:
        return None
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", raw, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


def confirm(key: str, title: str, detail: str, run) -> str:
    """Park an irreversible action behind the on-screen CONFIRM button."""
    from core import confirm as gate
    return gate.request(key, title, detail, run)


def notify(title: str, message: str = "") -> None:
    """Ping the paired phone / ntfy. Best-effort; never raises."""
    try:
        from core import notify as _n
        _n.push(title, message)
    except Exception:
        pass
