"""
calendar_control — see your schedule and add events by voice (Google Calendar).
Setup: see plugins/_google_core.py.
"""
from __future__ import annotations

import datetime as dt

from plugins._google_core import service, GoogleNotReady

PLUGIN = {
    "name": "calendar_control",
    "description": (
        "Google Calendar: list upcoming events, add an event, delete an event, "
        "or find the next free slot. Actions: list, add, delete, free. Use ISO "
        "local times like 2026-10-20T15:30:00 computed from the current date."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list | add | delete | free"},
            "days": {"type": "INTEGER", "description": "For list: how many days ahead (default 3)"},
            "title": {"type": "STRING", "description": "Event title (add)"},
            "start": {"type": "STRING", "description": "Start, ISO local time e.g. 2026-10-20T15:30:00 (add)"},
            "duration_minutes": {"type": "INTEGER", "description": "Length in minutes (add/free). Default 60"},
            "description": {"type": "STRING", "description": "Optional notes (add)"},
            "event_id": {"type": "STRING", "description": "Event id from a list (delete)"},
        },
        "required": ["action"],
    },
}


def _rfc(d: dt.datetime) -> str:
    return d.astimezone().isoformat()


def _fmt(ev: dict) -> str:
    s = ev.get("start", {})
    when = s.get("dateTime") or s.get("date") or "?"
    try:
        d = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        when = d.strftime("%a %d %b %H:%M") if "T" in when else d.strftime("%a %d %b (all day)")
    except Exception:
        pass
    return f"{when} — {ev.get('summary', '(untitled)')} (id {ev.get('id')})"


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    try:
        svc = service("calendar", "v3")
        now = dt.datetime.now().astimezone()

        if action == "list":
            days = max(1, min(30, int(parameters.get("days") or 3)))
            evs = svc.events().list(calendarId="primary", timeMin=_rfc(now),
                                    timeMax=_rfc(now + dt.timedelta(days=days)),
                                    singleEvents=True, orderBy="startTime",
                                    maxResults=25).execute().get("items", [])
            return "\n".join(_fmt(e) for e in evs) if evs else f"Nothing scheduled in the next {days} day(s)."

        if action == "add":
            title, start = (parameters.get("title") or "").strip(), (parameters.get("start") or "").strip()
            if not title or not start:
                return "I need a title and a start time."
            try:
                s = dt.datetime.fromisoformat(start)
            except ValueError:
                return f"I couldn't read the time '{start}'. Give it as 2026-10-20T15:30:00."
            if s.tzinfo is None:
                s = s.astimezone()
            mins = max(5, int(parameters.get("duration_minutes") or 60))
            body = {"summary": title, "description": parameters.get("description") or "",
                    "start": {"dateTime": _rfc(s)}, "end": {"dateTime": _rfc(s + dt.timedelta(minutes=mins))}}
            ev = svc.events().insert(calendarId="primary", body=body).execute()
            return f"Added '{title}' on {s.strftime('%A %d %B at %H:%M')} for {mins} minutes (id {ev.get('id')})."

        if action == "delete":
            eid = (parameters.get("event_id") or "").strip()
            if not eid:
                return "Which event? I need its id from a list."
            svc.events().delete(calendarId="primary", eventId=eid).execute()
            return "Event deleted."

        if action == "free":
            mins = max(5, int(parameters.get("duration_minutes") or 60))
            horizon = now + dt.timedelta(days=7)
            busy = svc.freebusy().query(body={"timeMin": _rfc(now), "timeMax": _rfc(horizon),
                                              "items": [{"id": "primary"}]}).execute()
            blocks = [(dt.datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                       dt.datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
                      for b in busy["calendars"]["primary"]["busy"]]
            cur = now + dt.timedelta(minutes=15 - now.minute % 15)
            while cur < horizon:
                if 9 <= cur.hour < 18 and cur.weekday() < 5:
                    end = cur + dt.timedelta(minutes=mins)
                    if end.hour < 18 or (end.hour == 18 and end.minute == 0):
                        if all(end <= b0 or cur >= b1 for b0, b1 in blocks):
                            return f"Next free {mins}-minute slot: {cur.strftime('%A %d %B at %H:%M')} (working hours 9-18)."
                cur += dt.timedelta(minutes=15)
            return "I found no free slot in working hours this week."

        return "Unknown action. Use list, add, delete or free."
    except GoogleNotReady as e:
        return f"Sir, {e}"
    except Exception as e:
        return f"Sir, Calendar failed: {e}"
