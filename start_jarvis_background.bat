@echo off
REM Runs JARVIS hidden in the system tray (no console window). Say "Hey Jarvis".
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw main.py --background) || (start "" /min python main.py --background)
