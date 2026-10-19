"""
video_gen — make short videos and animations from a description.

Two engines, tried in this order when mode is "auto":

  1. Veo (Google's video model) through the Gemini API. Real generated footage,
     usually 4-8 seconds. Needs a key with Veo access (paid tier); when it
     isn't available the plugin falls on engine 2 without failing.

  2. A local animation studio that needs nothing but this app's own packages:
     Gemini writes a short script, image_gen paints each scene, and OpenCV
     animates it with slow pans and zooms and fades between scenes. If
     ffmpeg is on PATH and narration is on, a Gemini voice-over is mixed in.

Videos are saved to ~/Documents/JARVIS Creations/videos and opened when done.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import wave
from pathlib import Path

from plugins._common import (output_dir, stamp, slug, open_file, log, notify,
                             gemini_client, ask_json)
from memory.config_manager import get_plugin_setting

PLUGIN = {
    "name": "video_gen",
    "description": (
        "Creates a NEW video or animation from a description: a short AI clip, "
        "an animated explainer, a story, a promo, a slideshow with narration. "
        "Takes 1-5 minutes; tell the user it is working. Do NOT use to PLAY an "
        "existing video (video_player) or to make a still image (image_gen)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "What the video should show / be about"},
            "mode": {"type": "STRING", "description": "auto (default), veo (real AI footage only) or animation (local animated scenes)"},
            "duration": {"type": "INTEGER", "description": "Target length in seconds for animation mode, 8-60. Default 20"},
            "aspect_ratio": {"type": "STRING", "description": "16:9 (default) or 9:16 for shorts/reels"},
            "narration": {"type": "BOOLEAN", "description": "Add a voice-over in animation mode (needs ffmpeg). Default true"},
        },
        "required": ["prompt"],
    },
    "behavior": "NON_BLOCKING",
}

PLUGIN_SETTINGS = {
    "namespace": "video_gen",
    "title": "Video generation",
    "fields": [
        {"key": "veo_model", "label": "Veo model", "type": "text",
         "default": "veo-3.0-fast-generate-001"},
        {"key": "tts_model", "label": "Narration (TTS) model", "type": "text",
         "default": "gemini-2.5-flash-preview-tts"},
        {"key": "tts_voice", "label": "Narration voice", "type": "text", "default": "Kore"},
        {"key": "auto_open", "label": "Open video when done", "type": "toggle", "default": True},
    ],
}


def _cfg(key, default):
    v = get_plugin_setting("video_gen", key, default)
    return default if v in (None, "") else v


# ── engine 1: Veo ────────────────────────────────────────────────────────────
def _veo(prompt: str, aspect: str, dest: Path, player) -> tuple[bool, str]:
    try:
        from google.genai import types
        client = gemini_client(120_000)
        op = client.models.generate_videos(
            model=str(_cfg("veo_model", "veo-3.0-fast-generate-001")),
            prompt=prompt,
            config=types.GenerateVideosConfig(aspect_ratio=aspect),
        )
        deadline = time.time() + 600
        while not op.done:
            if time.time() > deadline:
                return False, "Veo took longer than 10 minutes"
            time.sleep(10)
            op = client.operations.get(op)
        vids = getattr(op.response, "generated_videos", None) or []
        if not vids:
            return False, "Veo returned no video (the prompt may have been filtered)"
        client.files.download(file=vids[0].video)
        vids[0].video.save(str(dest))
        return dest.exists() and dest.stat().st_size > 0, ""
    except Exception as e:
        return False, str(e)[:200]


# ── engine 2: local animation ────────────────────────────────────────────────
def _script(prompt: str, scenes: int, narrate: bool) -> list[dict]:
    ask = (
        f"Write a {scenes}-scene script for a short video about: {prompt}\n"
        "Return ONLY JSON: a list of objects with keys "
        '"image" (a vivid, self-contained image-generation prompt for the scene, no text in the image), '
        '"caption" (a short on-screen caption, max 8 words), '
        '"narration" (one or two spoken sentences, in the same language as the request).'
    )
    data = ask_json(ask, timeout_ms=40_000)
    if isinstance(data, dict):
        data = data.get("scenes") or next(iter(data.values()), [])
    out = []
    for item in (data or []):
        if isinstance(item, dict) and item.get("image"):
            out.append({"image": str(item["image"]),
                        "caption": str(item.get("caption") or ""),
                        "narration": str(item.get("narration") or "")})
    if not out:
        out = [{"image": prompt, "caption": prompt[:40], "narration": prompt}]
    return out[:scenes]


def _title_card(caption: str, size: tuple[int, int], idx: int):
    """Fallback frame when an image could not be painted."""
    from PIL import Image, ImageDraw, ImageFont
    import colorsys
    w, h = size
    r, g, b = [int(255 * c) for c in colorsys.hsv_to_rgb((idx * 0.17) % 1, 0.55, 0.45)]
    img = Image.new("RGB", size, (r, g, b))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(24, h // 14))
    except Exception:
        font = ImageFont.load_default()
    words, lines, cur = caption.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if d.textlength(t, font=font) > w * 0.8 and cur:
            lines.append(cur); cur = wd
        else:
            cur = t
    lines.append(cur)
    y = h // 2 - len(lines) * (h // 14)
    for ln in lines:
        d.text(((w - d.textlength(ln, font=font)) / 2, y), ln, fill="white", font=font)
        y += h // 9
    return img


def _caption_overlay(frame, text: str):
    import cv2
    if not text:
        return frame
    h, w = frame.shape[:2]
    scale = max(0.6, w / 1400)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    x, y = max(10, (w - tw) // 2), h - int(h * 0.07)
    cv2.rectangle(frame, (x - 12, y - th - 12), (x + tw + 12, y + 12), (0, 0, 0), -1)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def _animate(images: list, captions: list[str], seconds_each: list[float],
             size: tuple[int, int], dest: Path, fps: int = 24) -> None:
    import cv2
    import numpy as np
    w, h = size
    vw = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    fade = int(fps * 0.5)
    prev_last = None
    for k, (img, cap, secs) in enumerate(zip(images, captions, seconds_each)):
        arr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
        ih, iw = arr.shape[:2]
        # cover-fit then leave 15% headroom for the pan/zoom
        s = max(w / iw, h / ih) * 1.15
        arr = cv2.resize(arr, (int(iw * s), int(ih * s)), interpolation=cv2.INTER_CUBIC)
        ih, iw = arr.shape[:2]
        n = max(1, int(secs * fps))
        zoom_in = (k % 2 == 0)
        for f in range(n):
            t = f / max(1, n - 1)
            z = 1.0 + 0.12 * (t if zoom_in else 1 - t)
            cw, ch = min(iw, int(w * z)), min(ih, int(h * z))
            px = (1 - t) if k % 3 == 0 else t
            x0 = int((iw - cw) * (0.25 + 0.5 * px))
            y0 = (ih - ch) // 2
            crop = arr[y0:y0 + ch, x0:x0 + cw]
            frame = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA)
            if prev_last is not None and f < fade:
                a = f / fade
                frame = cv2.addWeighted(prev_last, 1 - a, frame, a, 0)
            vw.write(_caption_overlay(frame.copy(), cap))
        prev_last = frame
    vw.release()


def _narrate(texts: list[str], dest_wav: Path) -> list[float]:
    """Synthesise one narration per scene with Gemini TTS. Returns each clip's
    duration in seconds; writes all of them, in order, to one 24 kHz wav."""
    from google.genai import types
    client = gemini_client(90_000)
    voice = str(_cfg("tts_voice", "Kore"))
    model = str(_cfg("tts_model", "gemini-2.5-flash-preview-tts"))
    durations, pcm_all = [], bytearray()
    for text in texts:
        pcm = b""
        if text.strip():
            try:
                r = client.models.generate_content(
                    model=model, contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
                pcm = r.candidates[0].content.parts[0].inline_data.data
            except Exception:
                pcm = b""
        dur = len(pcm) / 2 / 24000
        # 0.4 s of breathing room after every line
        pcm += b"\x00\x00" * int(24000 * 0.4)
        durations.append(len(pcm) / 2 / 24000)
        pcm_all += pcm
    with wave.open(str(dest_wav), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
        wf.writeframes(bytes(pcm_all))
    return durations


def _animation(prompt: str, duration: int, aspect: str, narrate: bool,
               dest: Path, player) -> tuple[bool, str]:
    try:
        from plugins import image_gen
    except Exception:
        import importlib
        image_gen = importlib.import_module("plugins.image_gen")
    try:
        from PIL import Image
        import io
        size = (1280, 720) if aspect != "9:16" else (720, 1280)
        n_scenes = max(3, min(10, round(duration / 5)))
        log(player, "JARVIS: Writing the script…")
        scenes = _script(prompt, n_scenes, narrate)

        images, caps = [], []
        for i, sc in enumerate(scenes):
            log(player, f"JARVIS: Painting scene {i + 1}/{len(scenes)}…")
            data, _err = image_gen.generate(sc["image"], "", "16:9" if aspect != "9:16" else "9:16")
            img = Image.open(io.BytesIO(data)) if data else _title_card(sc["caption"] or prompt, size, i)
            images.append(img)
            caps.append(sc["caption"])

        per = [max(3.0, duration / len(scenes))] * len(scenes)
        wav = None
        have_ffmpeg = shutil.which("ffmpeg") is not None
        if narrate and have_ffmpeg:
            log(player, "JARVIS: Recording the narration…")
            wav = dest.with_suffix(".wav")
            lens = _narrate([s["narration"] for s in scenes], wav)
            per = [max(3.0, d) for d in lens]
            # keep audio and picture the same length: pad shorter clips
            if abs(sum(per) - sum(lens)) > 0.01:
                _narrate_pad(wav, sum(per) - sum(lens))

        log(player, "JARVIS: Animating…")
        silent = dest.with_name(dest.stem + "_silent.mp4")
        _animate(images, caps, per, size, silent)

        if have_ffmpeg:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent)]
            if wav and wav.exists():
                cmd += ["-i", str(wav), "-c:a", "aac", "-shortest"]
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)]
            subprocess.run(cmd, check=True, timeout=300)
            silent.unlink(missing_ok=True)
            if wav:
                wav.unlink(missing_ok=True)
        else:
            silent.replace(dest)
        return dest.exists(), ""
    except Exception as e:
        return False, str(e)[:200]


def _narrate_pad(wav: Path, extra_seconds: float) -> None:
    try:
        with wave.open(str(wav), "rb") as r:
            params, frames = r.getparams(), r.readframes(r.getnframes())
        frames += b"\x00\x00" * int(24000 * extra_seconds)
        with wave.open(str(wav), "wb") as w:
            w.setparams(params); w.writeframes(frames)
    except Exception:
        pass


def produce(prompt: str, mode: str = "auto", duration: int = 20, aspect: str = "16:9",
            narrate: bool = True, player=None) -> tuple[Path | None, str]:
    """Make a video. Returns (path or None, note). Reusable by other plugins."""
    dest = output_dir("videos") / f"{stamp()}_{slug(prompt)}.mp4"
    notes = []
    if mode in ("auto", "veo"):
        log(player, "JARVIS: Asking Veo to film it — this takes a few minutes…")
        ok, err = _veo(prompt, aspect, dest, player)
        if ok:
            return dest, "Veo"
        notes.append(f"Veo unavailable ({err})")
        if mode == "veo":
            return None, f"Veo could not make it: {err}. Veo needs a paid Gemini API key."
    ok, err = _animation(prompt, duration, aspect, narrate, dest, player)
    if ok:
        return dest, "local animation" + (" (Veo wasn't available)" if notes else "")
    return None, f"{'; '.join(notes)} Animation: {err}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    prompt = (parameters.get("prompt") or "").strip()
    if not prompt:
        return "Sir, tell me what the video should be about."
    mode = (parameters.get("mode") or "auto").strip().lower()
    ar = str(parameters.get("aspect_ratio") or "")
    aspect = "9:16" if ar.replace(" ", "").startswith("9") else "16:9"
    try:
        duration = max(8, min(60, int(parameters.get("duration") or 20)))
    except Exception:
        duration = 20
    narrate = parameters.get("narration")
    narrate = True if narrate is None else bool(narrate)

    dest, note = produce(prompt, mode, duration, aspect, narrate, player)
    if dest is None:
        return f"Sir, I couldn't make the video. {note}"
    notify("Video ready", dest.name)
    if _cfg("auto_open", True):
        open_file(dest)
    return f"Your video ({note}) is ready and open on screen. It is saved at {dest}."
