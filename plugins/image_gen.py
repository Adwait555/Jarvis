"""
image_gen — create or edit pictures from a description.

Uses Gemini's native image model first and Imagen as a fallback. Pictures are
saved to ~/Documents/JARVIS Creations/images and opened on screen. Pass
`source_image` (a path) to EDIT an existing picture instead of creating one.

Models are configurable in Plugin Settings because Google renames them often.
"""
from __future__ import annotations

from pathlib import Path

from plugins._common import (output_dir, stamp, slug, open_file, log,
                             gemini_client)
from memory.config_manager import get_plugin_setting

PLUGIN = {
    "name": "image_gen",
    "description": (
        "Creates an image from a text description (art, logo, poster, photo, "
        "thumbnail, wallpaper), or edits an existing image file. Use for any "
        "'draw / generate / make a picture / edit this photo' request. Do NOT "
        "use for video (video_gen) or for describing an image (file_processor)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "Detailed description of the image, or the edit to apply"},
            "source_image": {"type": "STRING", "description": "Optional path of an existing image to edit"},
            "aspect_ratio": {"type": "STRING", "description": "1:1, 16:9, 9:16, 4:3 or 3:4 (Imagen fallback only). Default 1:1"},
            "count": {"type": "INTEGER", "description": "How many variations, 1-4. Default 1"},
        },
        "required": ["prompt"],
    },
    "behavior": "NON_BLOCKING",
}

PLUGIN_SETTINGS = {
    "namespace": "image_gen",
    "title": "Image generation",
    "fields": [
        {"key": "model", "label": "Gemini image model", "type": "text",
         "default": "gemini-2.5-flash-image"},
        {"key": "imagen_model", "label": "Imagen fallback model", "type": "text",
         "default": "imagen-4.0-generate-001"},
        {"key": "auto_open", "label": "Open picture when done", "type": "toggle", "default": True},
    ],
}


def _cfg(key, default):
    v = get_plugin_setting("image_gen", key, default)
    return default if v in (None, "") else v


def _gemini_image(client, prompt: str, source: Path | None) -> bytes | None:
    from google.genai import types
    contents: list = [prompt]
    if source is not None:
        from PIL import Image
        contents = [Image.open(source), prompt]
    resp = client.models.generate_content(
        model=str(_cfg("model", "gemini-2.5-flash-image")),
        contents=contents,
        config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
    )
    for cand in (resp.candidates or []):
        for part in (cand.content.parts or []):
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                return data
    return None


def _imagen(client, prompt: str, aspect: str) -> bytes | None:
    from google.genai import types
    resp = client.models.generate_images(
        model=str(_cfg("imagen_model", "imagen-4.0-generate-001")),
        prompt=prompt,
        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio=aspect),
    )
    imgs = getattr(resp, "generated_images", None) or []
    return imgs[0].image.image_bytes if imgs else None


def generate(prompt: str, source_image: str = "", aspect_ratio: str = "1:1") -> tuple[bytes | None, str]:
    """Return (png_bytes, error). Reusable by other plugins (video_gen)."""
    client = gemini_client(120_000)
    src = Path(source_image).expanduser() if source_image else None
    if src is not None and not src.is_file():
        return None, f"I can't find the image {source_image}."
    errors = []
    try:
        data = _gemini_image(client, prompt, src)
        if data:
            return data, ""
    except Exception as e:
        errors.append(f"gemini: {str(e)[:120]}")
    if src is None:
        try:
            data = _imagen(client, prompt, aspect_ratio)
            if data:
                return data, ""
        except Exception as e:
            errors.append(f"imagen: {str(e)[:120]}")
    return None, "; ".join(errors) or "the model returned no image"


def run(parameters: dict, player=None, session_memory=None) -> str:
    prompt = (parameters.get("prompt") or "").strip()
    if not prompt:
        return "Sir, tell me what to draw."
    source = (parameters.get("source_image") or "").strip()
    aspect = (parameters.get("aspect_ratio") or "1:1").strip()
    try:
        count = max(1, min(4, int(parameters.get("count") or 1)))
    except Exception:
        count = 1

    log(player, f"JARVIS: Creating {'an edit' if source else 'an image'}…")
    out_dir = output_dir("images")
    saved: list[Path] = []
    last_err = ""
    for i in range(count):
        data, err = generate(prompt, source, aspect)
        if not data:
            last_err = err
            continue
        path = out_dir / f"{stamp()}_{slug(prompt)}_{i + 1}.png"
        try:
            path.write_bytes(data)
            saved.append(path)
        except Exception as e:
            last_err = str(e)
    if not saved:
        return f"Sir, I could not create the image: {last_err}. Image models need a Gemini key with image access."
    if _cfg("auto_open", True):
        open_file(saved[0])
    log(player, f"JARVIS: Saved {len(saved)} image(s) to {out_dir}")
    names = ", ".join(p.name for p in saved)
    return f"Done. {len(saved)} image(s) saved in {out_dir} ({names}) and the first is open on screen."
