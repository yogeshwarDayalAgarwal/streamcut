@echo off
cd /d "%~dp0"
py -m venv .venv
call .venv\Scripts\activate
py -m pip install -r requirements.txt
py -m pip install -U yt-dlp
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg is required and must be on PATH. See https://ffmpeg.org/download.html
  pause
  exit /b 1
)
uvicorn main:app --host 127.0.0.1 --port 8000
