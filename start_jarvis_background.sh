#!/usr/bin/env bash
# Runs JARVIS in the background (tray icon). Say "Hey Jarvis".
cd "$(dirname "$0")"
nohup python3 main.py --background >/dev/null 2>&1 &
echo "JARVIS started in the background (pid $!)."
