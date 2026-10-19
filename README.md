# StreamCut — browser-based YouTube segment downloader MVP

StreamCut is a responsive browser UI with a YouTube embedded preview, start/end time inputs, draggable trim handles, a seek bar, source-quality lookup, and a backend job that uses yt-dlp + FFmpeg to process the selected range.

## Important limitations

- This is an MVP starter project, not a deployed public website.
- The browser preview uses YouTube's embedded player. Some videos do not allow embedding.
- The backend uses `yt-dlp` to inspect and process source media. Only use it with videos you own or are authorized to process, and follow YouTube's terms and applicable law. It does not bypass DRM or access controls.
- Format availability depends on the source. The interface excludes options below 720p and lists source heights found by yt-dlp. If the exact chosen height is absent, the backend may choose the nearest available height at or below the selected cap, but never intentionally below 720p.
- Some YouTube videos/livestreams may be unavailable, require authentication, or be incompatible with segment extraction. Keyframes can affect exact cut boundaries.
- This backend is intended for local testing or a trusted environment. Before public deployment, add authentication, per-user rate limits, request/job quotas, storage cleanup, concurrency limits, and operational monitoring. Do not expose an unrestricted download endpoint to the public internet.
- A YouTube embed does not grant a general download API; use the backend only for authorized media processing.

## Requirements

- Python 3.10+
- `yt-dlp`
- FFmpeg

### macOS (Homebrew)

```bash
brew install python yt-dlp ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### Windows

Install Python from https://www.python.org/downloads/windows/ and FFmpeg from https://ffmpeg.org/download.html. Install yt-dlp with:

```powershell
py -m pip install -U yt-dlp
```

Then in PowerShell, from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Make sure `yt-dlp` and `ffmpeg` are available on PATH. If they are not, the API reports the missing dependencies.

### Open the app

Visit http://127.0.0.1:8000 in your browser.

## Architecture

- `static/index.html`: responsive frontend with a YouTube IFrame API player, draggable trim timeline, quality picker, and job progress UI wired to the API below
- `main.py`: FastAPI endpoints for metadata, quality lookup, asynchronous segment processing, and file delivery
- `downloads/`: temporary outputs; create a cleanup policy before using beyond local testing

## API endpoints

- `GET /api/health`
- `POST /api/info` with `{"url":"https://www.youtube.com/watch?v=..."}`
- `POST /api/download` with `{"url":"...","start":"00:10:00","end":"00:15:00","quality":1080}`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/file`


## Deploy to GitHub + Render

1. Upload the contents of this folder to the root of your GitHub repository (not the ZIP itself).
2. In Render, choose **New + → Web Service**, connect your GitHub account, and select this repository.
3. Set **Language/Runtime** to **Docker** if Render does not detect the Dockerfile automatically.
4. Leave Dockerfile path as `./Dockerfile`, choose a region, and create the service.
5. Render will build the Docker image, install FFmpeg and Python dependencies, then start the app on the port supplied by Render.
6. Open the generated `https://...onrender.com` URL and test `/api/health`.

The `render.yaml` file is an optional Blueprint configuration. If you create the service manually, use the settings above.

### Public deployment warning

This MVP does not include user authentication or usage quotas. Anyone who discovers the public URL may be able to submit processing requests. Do not use it as an unrestricted public service until access controls, rate limits, concurrent-job limits, and temporary-file cleanup are implemented. For a private editor workflow, add a shared access gate before sharing the URL broadly.

Render free instances can sleep while idle, and their local filesystem is ephemeral. Large/long jobs may exceed free-instance resource or timeout limits. Test with a short clip first.
