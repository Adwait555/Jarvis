"""
inbox_triage — "what needs my attention?" for your email, with replies drafted
for you to approve.

  scan   : reads your unread mail, ranks it (urgent / needs reply / FYI /
           ignore) and speaks the short list. Each item gets a number.
  draft  : writes a reply to item N in your voice and saves it as a Gmail
           DRAFT. Nothing is sent.
  send   : sends the last drafted reply for item N — only after you press
           CONFIRM on screen.

Email only for now: WhatsApp and Telegram expose no safe way to read your
chats, so replies there still go through send_message. Setup: _google_core.py.
"""
from __future__ import annotations

import json
import re
import time

from plugins._common import ask_json, log, confirm, base_dir
from plugins._google_core import service, GoogleNotReady
from plugins.gmail_control import fetch_messages, make_raw

PLUGIN = {
    "name": "inbox_triage",
    "description": (
        "Prioritises the user's unread email and handles replies. Actions: scan "
        "(rank unread mail, speak the important ones), draft (write reply to item "
        "number N, saved as a Gmail draft, optional 'instructions' for tone/content), "
        "send (send the drafted reply for item N after on-screen CONFIRM). Use for "
        "'anything important in my inbox?', 'reply to the second one'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "scan | draft | send"},
            "item": {"type": "INTEGER", "description": "Item number from the last scan (draft/send)"},
            "instructions": {"type": "STRING", "description": "What the reply should say, e.g. 'politely decline'"},
            "max_messages": {"type": "INTEGER", "description": "How many unread messages to review (default 10, max 20)"},
        },
        "required": ["action"],
    },
}

_STATE = base_dir() / "config" / "triage_state.json"


def _load() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "drafts": {}, "at": 0}


def _save(state: dict) -> None:
    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except Exception:
        pass


def _addr(from_header: str) -> str:
    m = re.search(r"<([^>]+)>", from_header)
    return (m.group(1) if m else from_header).strip()


def _scan(svc, n: int) -> str:
    msgs = fetch_messages(svc, "is:unread in:inbox -category:promotions -category:social", n)
    if not msgs:
        return "Your inbox is clear — no unread mail that matters."
    listing = "\n\n".join(f"[{i}] From: {m['from']}\nSubject: {m['subject']}\n{m['body'][:600]}"
                          for i, m in enumerate(msgs))
    verdict = ask_json(
        "You triage email for a busy person. For each message give JSON objects "
        '{"i": <number>, "priority": "urgent"|"reply"|"fyi"|"ignore", "summary": "<=15 words", '
        '"why": "<=8 words"}. Return ONLY a JSON list.\n\n' + listing, timeout_ms=40_000)
    rank = {"urgent": 0, "reply": 1, "fyi": 2, "ignore": 3}
    items = []
    for v in (verdict or []):
        try:
            m = msgs[int(v["i"])]
        except Exception:
            continue
        items.append({**{k: m[k] for k in ("id", "thread", "from", "subject", "message_id")},
                      "body": m["body"][:2500], "priority": v.get("priority", "fyi"),
                      "summary": v.get("summary", m["snippet"][:80])})
    if not items:      # model failed: fall back to plain order
        items = [{**{k: m[k] for k in ("id", "thread", "from", "subject", "message_id")},
                  "body": m["body"][:2500], "priority": "fyi", "summary": m["snippet"][:80]} for m in msgs]
    items.sort(key=lambda x: rank.get(x["priority"], 2))
    _save({"items": items, "drafts": {}, "at": time.time()})
    shown = [f"{i}. [{it['priority']}] {it['from'].split('<')[0].strip()}: {it['summary']}"
             for i, it in enumerate(items, 1) if it["priority"] != "ignore"]
    hidden = sum(1 for it in items if it["priority"] == "ignore")
    return ("Unread mail, most important first:\n" + "\n".join(shown)
            + (f"\n({hidden} low-value message(s) skipped.)" if hidden else ""))


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    try:
        svc = service("gmail", "v1")
        if action == "scan":
            return _scan(svc, max(3, min(20, int(parameters.get("max_messages") or 10))))

        state = _load()
        try:
            idx = int(parameters.get("item")) - 1
            item = state["items"][idx]
        except Exception:
            return "Which item? Run a scan first, then give me the number."

        if action == "draft":
            ins = (parameters.get("instructions") or "write a short, polite, useful reply").strip()
            reply = ask_json(
                'Write an email reply. Return ONLY JSON {"body": "..."}. Same language as '
                f"the original. No placeholders. Instruction: {ins}\n\n"
                f"Original from {item['from']}, subject {item['subject']}:\n{item['body']}",
                timeout_ms=40_000)
            body = (reply or {}).get("body", "").strip() if isinstance(reply, dict) else ""
            if not body:
                return "I couldn't write that reply — try again with instructions."
            subj = item["subject"] if item["subject"].lower().startswith("re:") else "Re: " + item["subject"]
            to = _addr(item["from"])
            svc.users().drafts().create(userId="me", body={"message": make_raw(
                to, subj, body, {"message_id": item["message_id"], "thread": item["thread"]})}).execute()
            state["drafts"][str(idx + 1)] = {"to": to, "subject": subj, "body": body}
            _save(state)
            return f"Draft saved for {to}. It says: {body[:400]}  — say 'send it' to send after you confirm."

        if action == "send":
            d = state["drafts"].get(str(idx + 1))
            if not d:
                return "There's no draft for that item yet. Ask me to draft a reply first."

            def _do() -> str:
                try:
                    s = service("gmail", "v1")
                    s.users().messages().send(userId="me", body=make_raw(
                        d["to"], d["subject"], d["body"],
                        {"message_id": item["message_id"], "thread": item["thread"]})).execute()
                    log(player, f"SYS: Reply sent to {d['to']}")
                    return "Reply sent."
                except Exception as e:
                    return f"Reply failed: {e}"
            return confirm("triage_send", f"Send reply to {d['to']}", d["body"][:300], _do)

        return "Unknown action. Use scan, draft or send."
    except GoogleNotReady as e:
        return f"Sir, {e}"
    except Exception as e:
        return f"Sir, inbox triage failed: {e}"
