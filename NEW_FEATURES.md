# Mark LV+ — what was added

Ten features, all as drop-in plugins (plus small hooks in `main.py`, `core/confirm.py`,
`dashboard/server.py` and `dashboard/static/app.html`). **No new pip packages are needed** —
everything uses what `requirements.txt` already installs. Optional: `ffmpeg` on PATH
(for video narration and H.264 output).

| # | Feature | File | Say… |
|---|---|---|---|
| 1 | Image generation & editing | `plugins/image_gen.py` | "Draw a neon samurai poster" / "make this photo black-and-white" |
| 2 | Video & animation generation | `plugins/video_gen.py` | "Make a 30-second animated explainer about black holes" |
| 3 | Upload to Drive / YouTube | `plugins/upload_media.py` | "Upload the video you just made to YouTube" |
| 4 | Gmail + Calendar | `plugins/gmail_control.py`, `calendar_control.py` | "Any unread email?" / "Book dentist Tuesday 3pm" |
| 5 | Screen agent (multi-step) | `plugins/screen_agent.py` | "In Excel, bold the header row and save" |
| 6 | Document search | `plugins/doc_search.py` | "Index my Documents folder" / "what did the lease say about the deposit?" |
| 8 | Modes / personas | `plugins/persona.py` | "Switch to study mode" / "create a mode called gym…" |
| 9 | Spotify | `plugins/spotify.py` | "Play Daft Punk" / "next song" / "volume 30" |
| 11 | Inbox triage + replies | `plugins/inbox_triage.py` | "What's important in my inbox?" / "reply to number 2, decline politely" |
| 14 | Phone dashboard upgrades | `dashboard/*`, `core/notify.py` | 🖥 button = live screen on your phone; push notifications |

## One-time setup

**Image / video / doc search / modes / screen agent** — nothing. They use your existing Gemini key.
* Veo (real AI video) needs a paid Gemini key. Without it, `video_gen` automatically makes an
  animation locally (script → painted scenes → pans/zooms → optional narration).
* Model names are editable in ⚙ → Plugin settings, because Google renames them often.

**Gmail, Calendar, Drive, YouTube** (one sign-in for all four, free):
1. console.cloud.google.com → new project → enable *Gmail API, Google Calendar API, Google Drive API, YouTube Data API v3*.
2. OAuth consent screen → External → add your own Google account as a *Test user*.
3. Credentials → Create → OAuth client ID → *Desktop app* → download the JSON →
   save as `config/google_credentials.json`.
4. Ask JARVIS to "check my email". A browser opens once. Approve.
   (Unverified-app warning is normal for your own project: Advanced → continue.)

**Spotify:** developer.spotify.com/dashboard → create app → Redirect URI
`http://127.0.0.1:8888/callback` → paste Client ID in Plugin settings → Spotify.
Playback control needs Premium + Spotify open on a device.

**Phone push notifications (optional):** install the free *ntfy* app, pick a long random topic
name, subscribe to it, and add `"ntfy_topic": "your-random-topic"` to `config/api_keys.json`.
The paired phone dashboard also gets in-page alerts without this.

## Safety design
* **Sending email and uploading to YouTube always wait for the on-screen CONFIRM button** (the same
  gate used for shutdown) — the model cannot press it. Drafts never send. YouTube defaults to private.
* The screen agent has a step cap, a corner-of-screen abort (slam the mouse into a corner), a
  "stop" action, and is told never to type passwords/card numbers or confirm purchases/sending.
* Document search stores its index locally (`config/doc_index/`); only passage text is sent to
  Gemini for embedding, only for folders you index.
* Google/Spotify tokens live in `config/` (all added to `.gitignore`).

## Not done / honest limits
* Inbox triage is **email only**. WhatsApp/Telegram have no safe API for reading your chats.
* Written and syntax-checked here, and the local animation engine was run end-to-end, but the
  Google/Spotify/Gemini calls and the phone dashboard could not be exercised in this sandbox
  (no internet to those services, no FastAPI install). Expect to fix small API-name drift on first run.
* The doc index is a JSON file — fine for thousands of passages, slow past ~100k.


---

# Round 2 additions

| Feature | Where | Say / do |
|---|---|---|
| **Self-improvement** — JARVIS writes its own plugins | `plugins/self_improve.py` | "Learn how to check bitcoin prices" → reads code → "install bitcoin_price" → CONFIRM → restart |
| **Content pipeline** — idea → video + thumbnail + title/description/tags (+ optional YouTube upload) | `plugins/content_pipeline.py` | "Make a YouTube short about why the sky is blue and upload it" |
| **Background mode + "Hey Jarvis"** | `ui.py`, `main.py`, `enable_wake_word.py`, `start_jarvis_background.*` | see below |
| **Play any song** (Spotify, with fallbacks) | `plugins/spotify.py` | "Play Blinding Lights" / "play something chill" |
| **Android** | `ANDROID.md`, installable dashboard | see `ANDROID.md` |

## Run in the background and wake with "Hey Jarvis"
```
python enable_wake_word.py              # once: installs the local wake-word model and turns it on
start_jarvis_background.bat             # Windows (or:  python main.py --background)
./start_jarvis_background.sh            # macOS / Linux
```
* No window at start — a **tray icon** instead (right-click: Show, Hide, "Show window on wake word", Quit).
* Closing the window **hides it to the tray**; JARVIS keeps listening. Quit from the tray menu.
* Say **"Hey Jarvis"** → it wakes, listens, answers; it sleeps again after 2 minutes of silence.
  While asleep it streams nothing to Google — detection is local.
* **⚙ → AUTO-START** now starts it hidden at login (`--background`).
* If the tray isn't available on your system, it falls back to showing the window.

## Spotify: playing any song
* With the Spotify app set up (Client ID + Premium): searches Spotify, launches the app if it's closed, and plays.
* Vague asks ("something chill", "workout music") pick a playlist instead of a single track.
* Without Premium or without setup: it opens the song in the desktop app with autoplay, and if that
  doesn't start it, the screen agent clicks Play for you. Needs the Spotify desktop app installed.

## Self-improvement safety (read this)
The assistant writing code that then runs on your PC is the riskiest feature here. Controls:
generated code is saved to `plugins/_pending/` (never loaded), must pass a no-run static check,
is test-imported in a separate time-limited process, is **opened on screen for you to read**,
shows a list of risky calls it makes, and installs only after you press **CONFIRM**. It cannot
overwrite bundled plugins, and removed ones go to `plugins/_removed/`. **Read the code before
confirming** — if you can't judge it, cancel.
