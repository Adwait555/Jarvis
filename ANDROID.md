# JARVIS on your Android phone

**Honest summary first.** JARVIS's brain (voice, tools, screen control) runs on your **PC**, because
it controls the PC and uses a desktop window. What you can have on Android today is a **remote
JARVIS app** — an installable web app that talks to the JARVIS running on your PC: type or speak
commands, see the live screen, send files, get notifications. A *fully standalone* JARVIS inside
the phone (with its own "Hey Jarvis" wake word) would be a separate native app — see "Option D".

## Option A — same Wi-Fi (works in 2 minutes)
1. PC: start JARVIS (`python main.py`). Make sure the PC and phone are on the same Wi-Fi.
2. In JARVIS press **⚙ SETUP → Remote Control**. A QR code appears.
3. Phone: scan it with the camera (it opens in Chrome). Your browser may warn about the
   certificate — it is your own PC's self-made certificate: **Advanced → Proceed**.
4. In Chrome tap **⋮ → Install app** (or **Add to Home screen**). JARVIS is now an icon on your
   phone that opens full-screen. The phone remembers its pairing.
5. In the app: 🎤 talks, 📎 sends files to the PC, 🖥 shows the PC screen live, WAKE wakes it.

Windows may ask to allow Python through the firewall: choose **Private networks**.

## Option B — from anywhere (outside your home)
Use **Tailscale** (free): install it on the PC and the phone, sign in to the same account, then
open `https://<your-PC-tailscale-name-or-100.x.x.x>:8000` on the phone. That is a private,
encrypted tunnel — nothing is exposed to the internet. **Do not port-forward the dashboard on
your router.** Pair once through the QR flow (open the Remote Control page on the PC and use the
"manual URL" it shows, replaced with your Tailscale address).

## Option C — keep the PC side always on
Run JARVIS hidden so the phone always has something to talk to:
1. `python enable_wake_word.py` (once).
2. Turn on **⚙ → AUTO-START** — it now launches `main.py --background` at login: no window, a tray
   icon, "Hey Jarvis" listening on the PC.
3. Leave the PC on (set sleep to "never" while plugged in).
Limitation: pairing is forgotten when JARVIS restarts, so re-scan the QR after a reboot.

## Option D — a standalone native Android app (not built yet)
A real app that listens for "Hey Jarvis" on the phone and calls Gemini Live directly would be a
new Kotlin or Flutter project: foreground service for the wake word (Porcupine), Gemini Live
over WebSocket, and a small bridge so it can also send commands to your PC's JARVIS. It is a
decent-sized separate build — say the word and I'll scaffold it.

## What the phone cannot do
* A web app can't listen in the background with the screen off, so **there is no "Hey Jarvis" on
  the phone in Options A–C** — tap the 🎤 button instead.
* It can't control the phone itself (apps, texts, calls). That needs a native app or ADB.
