"""
upload_media — put a file on Google Drive or YouTube, hands-free.

Typical chain: "make me a video about X and upload it to YouTube" -> video_gen,
then this. YouTube uploads are PUBLIC-FACING, so they always wait for the
on-screen CONFIRM button, and default to private. Drive uploads go straight up.
Setup: see plugins/_google_core.py.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from plugins._common import log, confirm, output_dir, notify
from plugins._google_core import service, GoogleNotReady

PLUGIN = {
    "name": "upload_media",
    "description": (
        "Uploads a local file to Google Drive or YouTube. target=drive|youtube. "
        "YouTube needs the user to press CONFIRM and defaults to private "
        "visibility; only set privacy=public/unlisted if the user said so. If "
        "the user says 'the video you just made', use the path that video_gen "
        "reported, or file_path='latest_video'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target": {"type": "STRING", "description": "drive | youtube"},
            "file_path": {"type": "STRING", "description": "Path of the file, or 'latest_video' / 'latest_image'"},
            "title": {"type": "STRING", "description": "YouTube title or Drive file name (default: file name)"},
            "description": {"type": "STRING", "description": "YouTube description"},
            "privacy": {"type": "STRING", "description": "private (default) | unlisted | public — YouTube only"},
            "tags": {"type": "STRING", "description": "Comma-separated YouTube tags"},
        },
        "required": ["target", "file_path"],
    },
    "behavior": "NON_BLOCKING",
}


def _resolve(path: str) -> Path | None:
    key = (path or "").strip().lower()
    if key in ("latest_video", "latest_image"):
        d = output_dir("videos" if key == "latest_video" else "images")
        files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    p = Path(path).expanduser()
    return p if p.is_file() else None


def _drive(path: Path, title: str) -> str:
    from googleapiclient.http import MediaFileUpload
    svc = service("drive", "v3")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    f = svc.files().create(body={"name": title or path.name},
                           media_body=MediaFileUpload(str(path), mimetype=mime, resumable=True),
                           fields="id,webViewLink").execute()
    return f"Uploaded to Google Drive: {f.get('webViewLink', f.get('id'))}"


def _youtube(path: Path, title: str, desc: str, privacy: str, tags: str, thumbnail: Path | None = None) -> str:
    from googleapiclient.http import MediaFileUpload
    svc = service("youtube", "v3")
    body = {"snippet": {"title": (title or path.stem)[:100], "description": desc or "",
                        "tags": [t.strip() for t in (tags or "").split(",") if t.strip()]},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = svc.videos().insert(part="snippet,status", body=body,
                              media_body=MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _status, resp = req.next_chunk()
    extra = ""
    if thumbnail is not None and Path(thumbnail).is_file():
        try:
            svc.thumbnails().set(videoId=resp["id"], media_body=MediaFileUpload(str(thumbnail))).execute()
        except Exception:
            extra = " (thumbnail not set — YouTube needs a verified channel for custom thumbnails)"
    return f"Uploaded to YouTube ({privacy}): https://youtu.be/{resp['id']}{extra}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    target = (parameters.get("target") or "").strip().lower()
    path = _resolve(parameters.get("file_path") or "")
    if path is None:
        return "Sir, I can't find that file."
    title = (parameters.get("title") or "").strip()
    try:
        if target == "drive":
            log(player, f"JARVIS: Uploading {path.name} to Drive…")
            msg = _drive(path, title)
            notify("Upload finished", msg[:120])
            return msg
        if target == "youtube":
            privacy = (parameters.get("privacy") or "private").strip().lower()
            privacy = privacy if privacy in ("private", "unlisted", "public") else "private"
            desc, tags = parameters.get("description") or "", parameters.get("tags") or ""

            def _do() -> str:
                try:
                    log(player, f"JARVIS: Uploading {path.name} to YouTube…")
                    msg = _youtube(path, title, desc, privacy, tags)
                except Exception as e:
                    msg = f"YouTube upload failed: {e}"
                log(player, f"SYS: {msg}")
                notify("Upload finished", msg[:120])
                return msg
            return confirm("youtube_upload", f"Upload to YouTube ({privacy})",
                           f"{path.name}\nTitle: {title or path.stem}", _do)
        return "Target must be 'drive' or 'youtube'."
    except GoogleNotReady as e:
        return f"Sir, {e}"
    except Exception as e:
        return f"Sir, the upload failed: {e}"
