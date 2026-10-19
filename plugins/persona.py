"""
persona — switch the assistant between modes (work, study, creator, …).

A mode is a block of extra instructions added to the system prompt: tone,
priorities, which tools to reach for first. Switching reconnects the live
session a few seconds later (the conversation is kept), because the system
prompt is fixed when a session starts.

Say: "switch to study mode", "what modes do you have?", "create a mode called
gym that is a tough, short-spoken coach". Custom modes persist.
"""
from __future__ import annotations

from memory.config_manager import get_plugin_config, save_plugin_config

BUILTIN = {
    "default": "",
    "work": (
        "MODE: WORK. Be brisk and professional. Lead with the answer, then one line of "
        "context. Prefer email, calendar and document tools. Offer to draft rather than "
        "chat. No jokes unless the user makes one."
    ),
    "study": (
        "MODE: STUDY. Act as a patient tutor. Explain step by step, check understanding "
        "with a short question, and use analogies. Never just hand over a final answer to "
        "homework — guide first, reveal on request. Suggest breaks every ~25 minutes."
    ),
    "creator": (
        "MODE: CREATOR. Act as a creative producer. Think in hooks, scenes and thumbnails. "
        "When the user has an idea, offer to turn it into an image (image_gen), a video "
        "(video_gen) and an upload (upload_media), and propose titles and descriptions. "
        "Be enthusiastic and concrete."
    ),
    "focus": (
        "MODE: FOCUS. Speak as little as possible: one short sentence per reply. Do not "
        "volunteer extras or small talk. Only do what is asked."
    ),
    "chill": (
        "MODE: CHILL. Relaxed, friendly and a little playful. Fine to chat, joke and "
        "suggest music or videos."
    ),
}

PLUGIN = {
    "name": "persona",
    "description": (
        "Switches the assistant's mode/personality: work, study, creator, focus, chill, "
        "default, or a custom one. Actions: set (name), list, create (name + instructions), "
        "delete (name). Use when the user says 'switch to X mode', 'be my study buddy', "
        "'what modes are there'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "set | list | create | delete"},
            "name": {"type": "STRING", "description": "Mode name"},
            "instructions": {"type": "STRING", "description": "For create: how the assistant should behave in this mode"},
        },
        "required": ["action"],
    },
}


def _custom() -> dict:
    c = get_plugin_config("persona").get("custom")
    return dict(c) if isinstance(c, dict) else {}


def _all() -> dict:
    return {**BUILTIN, **_custom()}


def active_name() -> str:
    n = str(get_plugin_config("persona").get("active") or "default")
    return n if n in _all() else "default"


def active_instructions() -> str:
    """Called by main.py when the live session's system prompt is built."""
    try:
        text = _all().get(active_name(), "")
        return f"[ACTIVE MODE: {active_name()}]\n{text}\n\n" if text else ""
    except Exception:
        return ""


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    name = (parameters.get("name") or "").strip().lower().replace(" ", "_")
    modes = _all()

    if action == "list":
        return f"Modes: {', '.join(sorted(modes))}. Active: {active_name()}."

    if action == "set":
        if name not in modes:
            return f"I don't have a '{name}' mode. Available: {', '.join(sorted(modes))}."
        save_plugin_config("persona", {"active": name})
        if player:
            try:
                player.write_log(f"SYS: Mode → {name}")
            except Exception:
                pass
        return f"[PERSONA_CHANGED] Mode set to {name}. It takes effect in a few seconds."

    if action == "create":
        ins = (parameters.get("instructions") or "").strip()
        if not name or not ins or name in BUILTIN:
            return "I need a new name (not a built-in one) and instructions."
        custom = _custom()
        custom[name] = f"MODE: {name.upper()}. {ins}"
        save_plugin_config("persona", {"custom": custom})
        return f"Mode '{name}' created. Say 'switch to {name} mode' to use it."

    if action == "delete":
        custom = _custom()
        if name not in custom:
            return "Only custom modes can be deleted."
        custom.pop(name)
        patch = {"custom": custom}
        if active_name() == name:
            patch["active"] = "default"
        save_plugin_config("persona", patch)
        return f"Mode '{name}' deleted."

    return "Unknown action. Use set, list, create or delete."
