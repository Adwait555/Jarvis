"""
spotify — play music by voice: "play some Daft Punk", "next song", "volume 40".

Setup (free, 2 minutes):
  1. developer.spotify.com/dashboard -> Create app.
  2. Add this Redirect URI exactly:  http://127.0.0.1:8888/callback
  3. Copy the Client ID into Settings -> Plugin settings -> Spotify.
  4. First command opens a browser to approve once.
Controlling playback needs Spotify Premium. If Spotify isn't running, it is
launched for you. With NO setup at all (no Client ID) or without Premium, "play X"
still works on the desktop app: it opens the song (spotify:...:play) and, if that
doesn't start it, lets the screen agent click Play for you.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser

import requests

from plugins._common import base_dir, open_file
from memory.config_manager import get_plugin_setting

PLUGIN = {
    "name": "spotify",
    "description": (
        "Controls Spotify. Actions: play (query + optional kind track|artist|album|playlist), "
        "pause, resume, next, previous, volume (level 0-100), now_playing, shuffle (on/off). "
        "Use for any music request. Do NOT use for YouTube videos (video_player)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "play | pause | resume | next | previous | volume | now_playing | shuffle"},
            "query": {"type": "STRING", "description": "What to play: song, artist, album, playlist or mood"},
            "kind": {"type": "STRING", "description": "track (default) | artist | album | playlist"},
            "level": {"type": "INTEGER", "description": "Volume 0-100 for 'volume'"},
            "state": {"type": "STRING", "description": "on | off for 'shuffle'"},
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "spotify",
    "title": "Spotify",
    "fields": [{"key": "client_id", "label": "Spotify app Client ID", "type": "text"}],
}

_TOKEN = base_dir() / "config" / "spotify_token.json"
_REDIRECT = "http://127.0.0.1:8888/callback"
_SCOPE = "user-modify-playback-state user-read-playback-state user-read-currently-playing"
_API = "https://api.spotify.com/v1"
_lock = threading.Lock()


class _NoSetup(Exception):
    pass


def _client_id() -> str:
    cid = str(get_plugin_setting("spotify", "client_id", "") or "").strip()
    if not cid:
        raise _NoSetup("Spotify isn't set up. Create an app at developer.spotify.com/dashboard, "
                       "add redirect URI http://127.0.0.1:8888/callback, and paste its Client ID "
                       "into the Spotify plugin settings.")
    return cid


def _save_token(tok: dict) -> dict:
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    old = _load_token() or {}
    tok.setdefault("refresh_token", old.get("refresh_token"))
    _TOKEN.write_text(json.dumps(tok), encoding="utf-8")
    return tok


def _load_token() -> dict | None:
    try:
        return json.loads(_TOKEN.read_text(encoding="utf-8"))
    except Exception:
        return None


def _authorize(cid: str) -> dict:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(12)
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                got["code"] = q["code"][0]
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<h2>JARVIS is connected to Spotify. You can close this tab.</h2>")
        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 8888), H)
    srv.timeout = 1
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": _REDIRECT, "scope": _SCOPE,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
    webbrowser.open(url)
    deadline = time.time() + 180
    while "code" not in got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if "code" not in got:
        raise RuntimeError("Spotify sign-in was not completed in time")
    r = requests.post("https://accounts.spotify.com/api/token", data={
        "client_id": cid, "grant_type": "authorization_code", "code": got["code"],
        "redirect_uri": _REDIRECT, "code_verifier": verifier}, timeout=20)
    r.raise_for_status()
    return _save_token(r.json())


def _token() -> str:
    cid = _client_id()
    with _lock:
        tok = _load_token()
        if tok and tok.get("expires_at", 0) > time.time():
            return tok["access_token"]
        if tok and tok.get("refresh_token"):
            r = requests.post("https://accounts.spotify.com/api/token", data={
                "client_id": cid, "grant_type": "refresh_token",
                "refresh_token": tok["refresh_token"]}, timeout=20)
            if r.ok:
                return _save_token(r.json())["access_token"]
        return _authorize(cid)["access_token"]


def _call(method: str, path: str, **kw):
    return requests.request(method, _API + path, headers={"Authorization": f"Bearer {_token()}"},
                            timeout=15, **kw)


def _no_device(r) -> bool:
    return r.status_code == 404 or "NO_ACTIVE_DEVICE" in r.text


def _wake_device() -> bool:
    """Make a device active if one exists but is idle."""
    devs = _call("GET", "/me/player/devices").json().get("devices", [])
    if not devs:
        return False
    _call("PUT", "/me/player", json={"device_ids": [devs[0]["id"]], "play": False})
    time.sleep(1)
    return True


def _open_uri(uri: str) -> None:
    """Hand a spotify: URI (or https link) to the OS — launches/steers the desktop app."""
    import platform, subprocess
    try:
        if platform.system() == "Windows":
            os.startfile(uri)                                  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", uri])
        else:
            subprocess.Popen(["xdg-open", uri])
    except Exception:
        pass


def _launch_and_wait(seconds: int = 14) -> bool:
    """Start the Spotify app and wait until the Web API sees it as a device."""
    _open_uri("spotify:")
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(2)
        try:
            if _call("GET", "/me/player/devices").json().get("devices"):
                return True
        except Exception:
            pass
    return False


_MOOD_WORDS = ("music", "songs", "playlist", "mix", "vibes", "chill", "workout", "focus",
               "study", "party", "sleep", "relax", "romantic", "gaming", "driving", "something")


def _ui_play(query: str, uri: str | None, player) -> str:
    """No API / no Premium: drive the desktop app."""
    if uri:
        _open_uri(uri + ":play")            # desktop app auto-plays these on most versions
        time.sleep(4)
        return f"Opened it in Spotify and pressed play. If it isn't playing, say 'click play in Spotify'."
    _open_uri("spotify:search:" + urllib.parse.quote(query))
    time.sleep(5)
    try:
        from plugins import screen_agent
        return screen_agent.run({"action": "run", "max_steps": 6, "task":
            f"Spotify is open on a search for '{query}'. Click the first song result (or its play "
            "button) so it starts playing. Done once playback has started."}, player=player)
    except Exception as e:
        return f"I opened the Spotify search for '{query}' — press play on the top result ({e})."


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    try:
        if action == "play":
            query = (parameters.get("query") or "").strip()
            kind = (parameters.get("kind") or "").strip().lower()
            if kind not in ("track", "artist", "album", "playlist"):
                kind = "playlist" if any(w in query.lower() for w in _MOOD_WORDS) else "track"
            if not query:
                r = _call("PUT", "/me/player/play")
                return "Resumed." if r.status_code in (200, 202, 204) else "Nothing to resume."
            res = _call("GET", "/search", params={"q": query, "type": kind, "limit": 1}).json()
            items = res.get(kind + "s", {}).get("items", [])
            if not items and kind == "playlist":
                kind = "track"
                items = _call("GET", "/search", params={"q": query, "type": "track", "limit": 1}).json() \
                    .get("tracks", {}).get("items", [])
            items = [i for i in items if i]
            if not items:
                return f"I couldn't find '{query}' on Spotify."
            it = items[0]
            body = {"uris": [it["uri"]]} if kind == "track" else {"context_uri": it["uri"]}
            label = it["name"] + (f" by {it['artists'][0]['name']}" if kind in ("track", "album") else "")
            r = _call("PUT", "/me/player/play", json=body)
            if _no_device(r):
                if _wake_device() or _launch_and_wait():
                    r = _call("PUT", "/me/player/play", json=body)
            if r.status_code in (200, 202, 204):
                return f"Playing {label}."
            return f"{label}: " + _ui_play(query, it["uri"], player)       # no Premium

        if action in ("pause", "resume", "next", "previous"):
            verb = {"pause": ("PUT", "/me/player/pause"), "resume": ("PUT", "/me/player/play"),
                    "next": ("POST", "/me/player/next"), "previous": ("POST", "/me/player/previous")}[action]
            r = _call(*verb)
            if _no_device(r):
                return "Spotify isn't open on any device."
            return "Done." if r.status_code in (200, 202, 204) else f"Spotify said no ({r.status_code}). Premium is required."

        if action == "volume":
            lvl = max(0, min(100, int(parameters.get("level") if parameters.get("level") is not None else 50)))
            r = _call("PUT", "/me/player/volume", params={"volume_percent": lvl})
            return f"Spotify volume {lvl}." if r.status_code in (200, 202, 204) else "I couldn't change Spotify's volume."

        if action == "shuffle":
            on = str(parameters.get("state") or "on").lower() in ("on", "true", "yes")
            r = _call("PUT", "/me/player/shuffle", params={"state": str(on).lower()})
            return f"Shuffle {'on' if on else 'off'}." if r.status_code in (200, 202, 204) else "I couldn't change shuffle."

        if action == "now_playing":
            r = _call("GET", "/me/player/currently-playing")
            if r.status_code != 200 or not r.text:
                return "Nothing is playing."
            it = r.json().get("item") or {}
            return f"{it.get('name', 'Unknown')} by {', '.join(a['name'] for a in it.get('artists', []))}."

        return "Unknown action."
    except _NoSetup as e:
        if action == "play" and (parameters.get("query") or "").strip():
            return _ui_play((parameters.get("query") or "").strip(), None, player)
        return f"Sir, {e}"
    except Exception as e:
        return f"Sir, Spotify failed: {e}"
