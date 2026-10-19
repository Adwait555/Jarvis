"""
Shared Google sign-in for gmail_control, calendar_control, upload_media and
inbox_triage. One consent screen, one token, all four plugins.

ONE-TIME SETUP (about 5 minutes, free):
  1. console.cloud.google.com -> create a project.
  2. APIs & Services -> Library -> enable: Gmail API, Google Calendar API,
     Google Drive API, YouTube Data API v3.
  3. APIs & Services -> OAuth consent screen -> External -> add yourself as a
     Test user.
  4. Credentials -> Create credentials -> OAuth client ID -> "Desktop app".
     Download the JSON and save it as  config/google_credentials.json
  5. Ask JARVIS to "check my email". A browser opens once; approve; done.
"""
from __future__ import annotations

import threading
from pathlib import Path

from plugins._common import base_dir

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube.upload",
]

CRED_FILE = base_dir() / "config" / "google_credentials.json"
TOKEN_FILE = base_dir() / "config" / "google_token.json"
_lock = threading.Lock()

SETUP_HELP = (
    "Google is not set up yet. Create an OAuth 'Desktop app' client in Google "
    "Cloud Console (enable Gmail, Calendar, Drive and YouTube APIs), download its "
    "JSON, and save it as config/google_credentials.json in the JARVIS folder. "
    "The steps are at the top of plugins/_google_core.py."
)


class GoogleNotReady(Exception):
    pass


def credentials():
    """Valid credentials, refreshing or running the browser consent as needed."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise GoogleNotReady("Missing packages: pip install google-api-python-client "
                             "google-auth-oauthlib") from e

    with _lock:
        creds = None
        if TOKEN_FILE.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            except Exception:
                creds = None
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except Exception:
                creds = None
        if not CRED_FILE.exists():
            raise GoogleNotReady(SETUP_HELP)
        flow = InstalledAppFlow.from_client_secrets_file(str(CRED_FILE), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True,
                                      authorization_prompt_message="")
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return creds


def service(name: str, version: str):
    from googleapiclient.discovery import build
    return build(name, version, credentials=credentials(), cache_discovery=False)


def is_signed_in() -> bool:
    return TOKEN_FILE.exists()
