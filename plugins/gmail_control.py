"""
gmail_control — read, search, draft and send email by voice.

Sending always waits for the on-screen CONFIRM button (the model cannot press
it). Setup: see plugins/_google_core.py.
"""
from __future__ import annotations

import base64
import re
from email.message import EmailMessage

from plugins._common import log, confirm
from plugins._google_core import service, GoogleNotReady

PLUGIN = {
    "name": "gmail_control",
    "description": (
        "Gmail: list unread mail, search, read a message, draft or send an email. "
        "Actions: unread, search, read, draft, send. 'send' needs the user to press "
        "CONFIRM on screen. For a prioritised summary + suggested replies use "
        "inbox_triage instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "unread | search | read | draft | send"},
            "query": {"type": "STRING", "description": "Gmail search query for 'search' (e.g. 'from:bob newer_than:7d')"},
            "message_id": {"type": "STRING", "description": "Message id for 'read'"},
            "to": {"type": "STRING", "description": "Recipient address for draft/send"},
            "subject": {"type": "STRING", "description": "Subject for draft/send"},
            "body": {"type": "STRING", "description": "Body text for draft/send, written in the user's language"},
            "max_results": {"type": "INTEGER", "description": "How many messages to list (default 5, max 15)"},
        },
        "required": ["action"],
    },
}


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _body_text(payload: dict) -> str:
    """Plain-text body of a Gmail message payload (falls back to stripped HTML)."""
    def walk(part):
        mt, data = part.get("mimeType", ""), part.get("body", {}).get("data")
        if mt == "text/plain" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            r = walk(sub)
            if r:
                return r
        return ""
    text = walk(payload)
    if not text:
        def walk_html(part):
            data = part.get("body", {}).get("data")
            if part.get("mimeType") == "text/html" and data:
                return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
            for sub in part.get("parts", []) or []:
                r = walk_html(sub)
                if r:
                    return r
            return ""
        html = walk_html(payload)
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", "", html))
    return re.sub(r"\s+", " ", text).strip()


def fetch_messages(svc, query: str, limit: int) -> list[dict]:
    """Metadata + short snippet for the newest matching messages."""
    ids = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute().get("messages", [])
    out = []
    for m in ids:
        full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        out.append({
            "id": m["id"], "thread": full.get("threadId"),
            "from": _header(full, "From"), "subject": _header(full, "Subject") or "(no subject)",
            "date": _header(full, "Date"), "message_id": _header(full, "Message-ID"),
            "snippet": full.get("snippet", ""), "body": _body_text(full.get("payload", {}))[:4000],
        })
    return out


def make_raw(to: str, subject: str, body: str, reply_to: dict | None = None) -> dict:
    em = EmailMessage()
    em["To"], em["Subject"] = to, subject
    if reply_to and reply_to.get("message_id"):
        em["In-Reply-To"] = reply_to["message_id"]
        em["References"] = reply_to["message_id"]
    em.set_content(body)
    raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
    msg = {"raw": raw}
    if reply_to and reply_to.get("thread"):
        msg["threadId"] = reply_to["thread"]
    return msg


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    try:
        svc = service("gmail", "v1")
        n = max(1, min(15, int(parameters.get("max_results") or 5)))

        if action in ("unread", "search"):
            q = "is:unread in:inbox" if action == "unread" else (parameters.get("query") or "")
            msgs = fetch_messages(svc, q, n)
            if not msgs:
                return "No matching messages."
            lines = [f"{i}. From {m['from']} — {m['subject']} (id {m['id']}): {m['snippet'][:120]}"
                     for i, m in enumerate(msgs, 1)]
            return f"{len(msgs)} message(s):\n" + "\n".join(lines)

        if action == "read":
            mid = (parameters.get("message_id") or "").strip()
            if not mid:
                return "Which message? I need its id from a list."
            full = svc.users().messages().get(userId="me", id=mid, format="full").execute()
            return (f"From {_header(full, 'From')}, subject {_header(full, 'Subject')}:\n"
                    f"{_body_text(full.get('payload', {}))[:3000]}")

        to = (parameters.get("to") or "").strip()
        subject = (parameters.get("subject") or "").strip() or "(no subject)"
        body = (parameters.get("body") or "").strip()

        if action == "draft":
            if not to or not body:
                return "I need a recipient and the message text."
            svc.users().drafts().create(userId="me", body={"message": make_raw(to, subject, body)}).execute()
            return f"Draft to {to} saved in Gmail. Nothing was sent."

        if action == "send":
            if not to or not body:
                return "I need a recipient and the message text."

            def _do() -> str:
                try:
                    s = service("gmail", "v1")
                    s.users().messages().send(userId="me", body=make_raw(to, subject, body)).execute()
                    log(player, f"SYS: Email sent to {to}")
                    return f"Email sent to {to}."
                except Exception as e:
                    log(player, f"SYS: Email failed — {e}")
                    return f"Email failed: {e}"
            return confirm("gmail_send", f"Send email to {to}", f"Subject: {subject}\n\n{body[:300]}", _do)

        return "Unknown action. Use unread, search, read, draft or send."
    except GoogleNotReady as e:
        return f"Sir, {e}"
    except Exception as e:
        return f"Sir, Gmail failed: {e}"
