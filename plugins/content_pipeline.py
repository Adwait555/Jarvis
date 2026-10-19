"""
content_pipeline — one sentence in, a finished video package out.

    "Make a YouTube short about why the sky is blue and upload it"

Steps (progress is logged and you get a notification at the end):
  1. plan      — Gemini writes the title, description, tags and a thumbnail idea
  2. video     — video_gen (Veo if your key allows it, else a narrated local animation)
  3. thumbnail — image_gen
  4. package   — everything saved together in ~/Documents/JARVIS Creations/packages/<name>/
                 (video, thumbnail, metadata.json, description.txt)
  5. upload    — only if you asked; YouTube always waits for the on-screen CONFIRM
                 button and defaults to PRIVATE so you can review it in YouTube Studio

Without upload=true nothing leaves your computer except the Gemini calls.
"""
from __future__ import annotations

import json
import shutil

from plugins._common import output_dir, stamp, slug, open_file, log, notify, ask_json, confirm

PLUGIN = {
    "name": "content_pipeline",
    "description": (
        "Makes a complete video package from one idea: script, video, thumbnail, title, "
        "description and tags, and optionally uploads it to YouTube (private by default, "
        "needs on-screen CONFIRM). Use for 'make a short/video about X and upload it', "
        "'create content about X'. Takes several minutes — tell the user it is working. "
        "For just a video use video_gen; for just an upload use upload_media."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "topic": {"type": "STRING", "description": "What the video is about"},
            "format": {"type": "STRING", "description": "short (vertical 9:16, ~25s) or standard (16:9, ~45s). Default short"},
            "duration": {"type": "INTEGER", "description": "Seconds, 10-60. Overrides the format's default"},
            "upload": {"type": "BOOLEAN", "description": "Upload to YouTube when finished (asks for CONFIRM). Default false"},
            "privacy": {"type": "STRING", "description": "private (default) | unlisted | public"},
            "style": {"type": "STRING", "description": "Optional look/tone, e.g. 'cinematic', 'cartoon', 'serious documentary'"},
        },
        "required": ["topic"],
    },
    "behavior": "NON_BLOCKING",
}


def _plan(topic: str, style: str, short: bool) -> dict:
    data = ask_json(
        "You are a YouTube producer. Return ONLY JSON with keys: "
        '"title" (<=70 chars, curiosity-driven, no clickbait lies), '
        '"description" (2-4 sentences + a call to action, same language as the topic), '
        '"tags" (list of 8-12 short tags), '
        '"video_prompt" (a clear brief for the video: hook in the first sentence, then the key points'
        f'{", fast-paced vertical short" if short else ""}), '
        '"thumbnail_prompt" (a bold, high-contrast thumbnail image prompt with NO text in the image).\n'
        f"Topic: {topic}\nStyle: {style or 'engaging, clear'}", timeout_ms=45_000)
    if not isinstance(data, dict) or not data.get("video_prompt"):
        data = {"title": topic[:70], "description": topic, "tags": [], "video_prompt": topic,
                "thumbnail_prompt": f"Bold, high-contrast thumbnail illustrating: {topic}"}
    return data


def run(parameters: dict, player=None, session_memory=None) -> str:
    topic = (parameters.get("topic") or "").strip()
    if not topic:
        return "Sir, what should the video be about?"
    short = str(parameters.get("format") or "short").lower().startswith("s")
    try:
        duration = max(10, min(60, int(parameters.get("duration") or (25 if short else 45))))
    except Exception:
        duration = 25 if short else 45
    privacy = str(parameters.get("privacy") or "private").lower()
    privacy = privacy if privacy in ("private", "unlisted", "public") else "private"
    want_upload = bool(parameters.get("upload"))
    aspect = "9:16" if short else "16:9"

    try:
        from plugins import video_gen, image_gen

        log(player, "JARVIS: [1/4] Planning the video…")
        plan = _plan(topic, str(parameters.get("style") or ""), short)

        log(player, "JARVIS: [2/4] Making the video — a few minutes…")
        video, note = video_gen.produce(plan["video_prompt"], "auto", duration, aspect, True, player)
        if video is None:
            return f"Sir, the video step failed: {note}"

        log(player, "JARVIS: [3/4] Painting the thumbnail…")
        thumb_bytes, terr = image_gen.generate(plan["thumbnail_prompt"], "", aspect)

        log(player, "JARVIS: [4/4] Packaging…")
        pkg = output_dir("packages") / f"{stamp()}_{slug(plan['title'])}"
        pkg.mkdir(parents=True, exist_ok=True)
        final_video = pkg / "video.mp4"
        shutil.copy2(video, final_video)
        thumb = None
        if thumb_bytes:
            thumb = pkg / "thumbnail.png"
            thumb.write_bytes(thumb_bytes)
        tags = [str(t) for t in (plan.get("tags") or [])][:15]
        (pkg / "metadata.json").write_text(json.dumps({**plan, "tags": tags, "topic": topic,
                                                       "engine": note}, indent=2, ensure_ascii=False), encoding="utf-8")
        (pkg / "description.txt").write_text(f"{plan['title']}\n\n{plan['description']}\n\n"
                                             + " ".join("#" + t.replace(" ", "") for t in tags), encoding="utf-8")
        open_file(pkg)
        notify("Content package ready", plan["title"])
        done = (f"Your package '{plan['title']}' is ready ({note}) in {pkg}"
                + ("" if thumb else f" — the thumbnail failed ({terr[:60]})") + ".")

        if not want_upload:
            return done + " Say 'upload it' if you want it on YouTube."

        from plugins import upload_media

        def _do() -> str:
            try:
                msg = upload_media._youtube(final_video, plan["title"], plan["description"], privacy,
                                            ",".join(tags), thumb)
            except Exception as e:
                msg = f"YouTube upload failed: {e}"
            log(player, f"SYS: {msg}")
            notify("Upload finished", msg[:120])
            return msg
        pending = confirm("pipeline_upload", f"Upload to YouTube ({privacy})",
                          f"{plan['title']}\n{plan['description'][:200]}", _do)
        return done + " " + pending
    except Exception as e:
        return f"Sir, the content pipeline failed: {e}"
