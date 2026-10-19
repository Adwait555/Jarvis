"""
One-time: install the "Hey Jarvis" wake word and turn it on.

    python enable_wake_word.py

Afterwards `python main.py --background` (or start_jarvis_background.bat/.sh)
runs JARVIS hidden in the system tray, listening for "Hey Jarvis". It never
streams audio to Google while asleep; detection is local.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.wake_word import install_and_download          # noqa: E402
from memory.config_manager import save_wake_word_enabled  # noqa: E402

ok, msg = install_and_download()
print(msg)
if ok:
    save_wake_word_enabled(True)
    print('Wake word enabled. Start JARVIS and say "Hey Jarvis".')
    sys.exit(0)
sys.exit(1)
