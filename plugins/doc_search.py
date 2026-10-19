"""
doc_search — ask questions about everything in your folders.

  index  : reads the documents in a folder (txt, md, code, csv, json, pdf, docx),
           cuts them into passages and embeds each with Gemini. Re-indexing only
           touches files that changed. Works while you keep talking.
  search : finds the passages that best match a question, with file names, so
           the assistant can answer from YOUR documents and tell you where it
           read it.
  status / forget : see what is indexed, or drop a folder from the index.

Everything is stored locally in config/doc_index/. Only the passage text is
sent to Gemini (for embedding) — and only for the folders you ask it to index.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from plugins._common import base_dir, log, gemini_client, notify

PLUGIN = {
    "name": "doc_search",
    "description": (
        "Searches the user's own documents by meaning. Actions: index (folder path: "
        "read & remember a folder), search (query: find relevant passages — then answer "
        "from them and name the file), status, forget (folder). Use for 'what did the "
        "contract say about…', 'find my notes on…'. For reading ONE named file use "
        "file_processor."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "index | search | status | forget"},
            "folder": {"type": "STRING", "description": "Folder path for index/forget"},
            "query": {"type": "STRING", "description": "Question or topic for search"},
            "top_k": {"type": "INTEGER", "description": "Passages to return (default 5, max 10)"},
        },
        "required": ["action"],
    },
    "behavior": "NON_BLOCKING",
}

_DIR = base_dir() / "config" / "doc_index"
_LOCK = threading.Lock()
_EMBED_MODEL = "gemini-embedding-001"
_TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css", ".java",
             ".c", ".cpp", ".h", ".go", ".rs", ".sql", ".yaml", ".yml", ".toml", ".ini", ".log", ".tex"}
_MAX_FILES, _MAX_BYTES = 1500, 8 * 1024 * 1024
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages", "dist", "build"}


def _read(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in _TEXT_EXT:
            return path.read_text(encoding="utf-8", errors="ignore")
        if ext == ".pdf":
            try:
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    return "\n".join((p.extract_text() or "") for p in pdf.pages[:200])
            except ImportError:
                from PyPDF2 import PdfReader
                return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages[:200])
        if ext == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    except Exception:
        return ""
    return ""


def _chunks(text: str, size: int = 900, overlap: int = 150):
    text = " ".join(text.split())
    i = 0
    while i < len(text):
        yield text[i:i + size]
        i += size - overlap


def _embed(client, texts: list[str], task: str) -> list[list[float]]:
    from google.genai import types
    out: list[list[float]] = []
    for i in range(0, len(texts), 50):
        r = client.models.embed_content(
            model=_EMBED_MODEL, contents=texts[i:i + 50],
            config=types.EmbedContentConfig(task_type=task, output_dimensionality=768))
        out += [e.values for e in r.embeddings]
    return out


def _index_path() -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR / "index.json"


def _load() -> dict:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}, "chunks": []}   # chunks: {file, text, vec}


def _save(idx: dict) -> None:
    _index_path().write_text(json.dumps(idx), encoding="utf-8")


def _index(folder: Path, player) -> str:
    client = gemini_client(90_000)
    with _LOCK:
        idx = _load()
        files = [p for p in folder.rglob("*")
                 if p.is_file() and p.suffix.lower() in (_TEXT_EXT | {".pdf", ".docx"})
                 and not any(part in _SKIP_DIRS for part in p.parts)
                 and p.stat().st_size <= _MAX_BYTES][:_MAX_FILES]
        changed = [p for p in files if idx["files"].get(str(p)) != p.stat().st_mtime]
        log(player, f"JARVIS: Indexing {len(changed)} new/changed file(s) of {len(files)}…")
        added = 0
        for n, p in enumerate(changed, 1):
            idx["chunks"] = [c for c in idx["chunks"] if c["file"] != str(p)]
            pieces = list(_chunks(_read(p)))[:400]
            if pieces:
                try:
                    vecs = _embed(client, pieces, "RETRIEVAL_DOCUMENT")
                except Exception as e:
                    log(player, f"SYS: Could not embed {p.name}: {str(e)[:80]}")
                    continue
                idx["chunks"] += [{"file": str(p), "text": t, "vec": v} for t, v in zip(pieces, vecs)]
                added += len(pieces)
            idx["files"][str(p)] = p.stat().st_mtime
            if n % 20 == 0:
                _save(idx)
        _save(idx)
    notify("Folder indexed", f"{len(changed)} files")
    return f"Indexed {len(changed)} file(s), {added} passages. {len(idx['files'])} files total are searchable."


def _search(query: str, k: int) -> str:
    import numpy as np
    idx = _load()
    if not idx["chunks"]:
        return "Nothing is indexed yet. Ask me to index a folder first."
    client = gemini_client(60_000)
    q = np.array(_embed(client, [query], "RETRIEVAL_QUERY")[0], dtype=np.float32)
    mat = np.array([c["vec"] for c in idx["chunks"]], dtype=np.float32)
    sims = (mat @ q) / (np.linalg.norm(mat, axis=1) * np.linalg.norm(q) + 1e-9)
    best = np.argsort(-sims)[:k]
    out = []
    for r, i in enumerate(best, 1):
        c = idx["chunks"][int(i)]
        out.append(f"[{r}] {Path(c['file']).name} (score {sims[i]:.2f}): {c['text'][:500]}")
    return "Most relevant passages (answer from these and name the file):\n" + "\n\n".join(out)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    try:
        if action == "index":
            folder = Path(parameters.get("folder") or "").expanduser()
            if not folder.is_dir():
                return "I can't find that folder."
            return _index(folder, player)
        if action == "search":
            q = (parameters.get("query") or "").strip()
            if not q:
                return "What should I search for?"
            return _search(q, max(1, min(10, int(parameters.get("top_k") or 5))))
        if action == "status":
            idx = _load()
            return f"{len(idx['files'])} files, {len(idx['chunks'])} passages indexed."
        if action == "forget":
            folder = str(Path(parameters.get("folder") or "").expanduser())
            with _LOCK:
                idx = _load()
                gone = [f for f in idx["files"] if f.startswith(folder)]
                for f in gone:
                    idx["files"].pop(f)
                idx["chunks"] = [c for c in idx["chunks"] if not c["file"].startswith(folder)]
                _save(idx)
            return f"Forgot {len(gone)} file(s)."
        return "Unknown action. Use index, search, status or forget."
    except Exception as e:
        return f"Sir, document search failed: {e}"
