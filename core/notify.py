"""
core/notify.py — tell the person something when they are not at the screen.

Two channels, both optional and both best-effort (a failure here must never
break the action that triggered it):

  * the phone dashboard, if a phone is paired — it gets a toast and, if the
    browser allowed it, a system notification;
  * ntfy.sh push — set "ntfy_topic" in config/api_keys.json to a long random
    string, install the free ntfy app on your phone and subscribe to the same
    topic. Works when the phone is on mobile data and the dashboard is closed.
    (Anyone who knows the topic can read the messages, so keep it unguessable
    and don't put secrets in notifications — this module never does.)
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
import sys

_loop = None
_broadcast = None


def bind(loop, broadcast) -> None:
    """Called once from main.py when the dashboard starts."""
    global _loop, _broadcast
    _loop, _broadcast = loop, broadcast


def _topic() -> str:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    try:
        return str(json.loads((base / "config" / "api_keys.json").read_text(encoding="utf-8"))
                   .get("ntfy_topic") or "").strip()
    except Exception:
        return ""


def push(title: str, message: str = "") -> None:
    """Fire and forget. Safe to call from any thread."""
    title, message = str(title)[:80], str(message)[:300]

    if _loop is not None and _broadcast is not None:
        try:
            asyncio.run_coroutine_threadsafe(
                _broadcast({"type": "notify", "title": title, "text": message}), _loop)
        except Exception:
            pass

    topic = _topic()
    if topic:
        def _send():
            try:
                import requests
                requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                              headers={"Title": title.encode("utf-8")}, timeout=8)
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()
