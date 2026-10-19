#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/macos/"
  read -r -p "Press Return to close..."
  exit 1
fi
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -r requirements.txt
if ! command -v yt-dlp >/dev/null 2>&1; then
  python -m pip install -U yt-dlp
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg is required. On macOS with Homebrew run: brew install ffmpeg"
  read -r -p "Press Return to close..."
  exit 1
fi
uvicorn main:app --host 127.0.0.1 --port 8000
