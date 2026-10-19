import os
import re
import shutil
import subprocess
import sys
import threading
import importlib.util
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
OUTPUT = Path(os.environ.get("STREAMCUT_OUTPUT_DIR", str(ROOT / "downloads"))).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="StreamCut", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

JOBS = {}
LOCK = threading.Lock()

def validate_youtube_url(url: str) -> str:
    url = url.strip()
    # Limit to standard YouTube URLs. This also reduces SSRF risk.
    patterns = [
        r"^https?://(?:www\.)?youtube\.com/watch\?v=[\w-]{11}(?:[&#].*)?$",
        r"^https?://youtu\.be/[\w-]{11}(?:[?&#].*)?$",
        r"^https?://(?:www\.)?youtube\.com/live/[\w-]{11}(?:[?&#].*)?$",
        r"^https?://(?:www\.)?youtube\.com/shorts/[\w-]{11}(?:[?&#].*)?$",
        r"^https?://m\.youtube\.com/watch\?v=[\w-]{11}(?:[&#].*)?$",
    ]
    if not any(re.match(p, url) for p in patterns):
        raise HTTPException(400, "Enter a valid YouTube video URL.")
    return url

def parse_timestamp(value: str) -> int:
    try:
        parts = [int(x) for x in value.strip().split(":")]
        if len(parts) == 3:
            h, m, s = parts
            if m > 59 or s > 59: raise ValueError()
            total = h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m, s = parts
            if s > 59: raise ValueError()
            total = m * 60 + s
        elif len(parts) == 1:
            total = parts[0]
        else:
            raise ValueError()
        if total < 0: raise ValueError()
        return total
    except Exception:
        raise HTTPException(400, f"Invalid timestamp: {value}. Use HH:MM:SS or MM:SS.")

class InfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    start: str
    end: str
    quality: int = Field(ge=720, le=4320)

def format_seconds(value: float) -> str:
    """Format a number of seconds as a plain decimal string for ffmpeg."""
    return f"{max(0.0, float(value)):.3f}"


def ytdlp_command() -> list[str]:
    """Return the yt-dlp invocation, preferring the installed Python module.

    Running ``python -m yt_dlp`` guarantees the same version that
    requirements.txt pins is used everywhere (locally and in the container),
    instead of relying on whatever happens to be on PATH. A system binary is
    only used as a last resort when the module is missing.
    """
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    if shutil.which("yt-dlp") is not None:
        return ["yt-dlp"]
    return []


def require_dependencies():
    missing = []
    if not ytdlp_command():
        missing.append("yt-dlp")
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    if missing:
        raise HTTPException(503, "Missing dependencies: " + ", ".join(missing) +
                            ". Install them with: brew install yt-dlp ffmpeg")

@app.get("/", response_class=HTMLResponse)
def home():
    return (STATIC / "index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    missing = []
    if not ytdlp_command():
        missing.append("yt-dlp")
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    return {"ok": not missing, "missing": missing}

@app.post("/api/info")
def video_info(req: InfoRequest):
    require_dependencies()
    url = validate_youtube_url(req.url)
    cmd = ytdlp_command() + ["--dump-single-json", "--no-warnings", "--skip-download", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Timed out while reading video information.")
    if result.returncode != 0:
        # Keep the UI message actionable while preserving the real cause in Render logs.
        detail = (result.stderr or result.stdout or "yt-dlp exited without an error message").strip()
        print(f"[StreamCut] yt-dlp metadata extraction failed for {url}: {detail[-5000:]}", flush=True)
        lowered = detail.lower()
        if "sign in to confirm" in lowered or "not a bot" in lowered or "po token" in lowered:
            message = "YouTube blocked server-side extraction with an authentication/bot check. The video can still play in the browser; check Render logs for details."
        elif "video unavailable" in lowered or "private video" in lowered:
            message = "YouTube reports this video as unavailable or private to the server. Check the video visibility and Render logs."
        elif "javascript runtime" in lowered or "challenge" in lowered:
            message = "YouTube extraction needs its JavaScript challenge runtime. Redeploy the latest StreamCut Docker image and check Render logs."
        else:
            message = "StreamCut could not extract video formats from YouTube. Check the Render logs for the underlying yt-dlp error."
        raise HTTPException(400, message)
    import json
    try:
        data = json.loads(result.stdout)
    except Exception:
        raise HTTPException(502, "Could not parse video information.")
    formats = data.get("formats", [])
    heights = sorted({int(f["height"]) for f in formats
                      if f.get("height") and f.get("vcodec") != "none" and int(f["height"]) >= 720})
    duration = data.get("duration")
    return {
        "id": data.get("id"),
        "title": data.get("title") or "YouTube video",
        "duration": duration,
        "thumbnail": data.get("thumbnail"),
        "qualities": heights,
        "message": None if heights else "No downloadable video formats at 720p or above were found."
    }

@app.post("/api/download")
def create_download(req: DownloadRequest):
    require_dependencies()
    url = validate_youtube_url(req.url)
    start_s, end_s = parse_timestamp(req.start), parse_timestamp(req.end)
    if end_s <= start_s:
        raise HTTPException(400, "End time must be later than start time.")
    if end_s - start_s > 4 * 3600:
        raise HTTPException(400, "Please select a segment of four hours or less.")
    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    # Select the requested height or the best available height at/above 720p,
    # never intentionally select a video stream below 720p.
    fmt = f"bestvideo[height={req.quality}][height>=720]+bestaudio/best[height={req.quality}][height>=720]/bestvideo[height<={req.quality}][height>=720]+bestaudio/best[height<={req.quality}][height>=720]"

    # Stage 1: fetch the media with yt-dlp's native downloader.
    #
    # We deliberately avoid yt-dlp's --download-sections here. That flag forces
    # the ffmpeg downloader, which cannot use yt-dlp's parallel fragment
    # downloading and measured ~18x slower (185 KB/s vs ~3.4 MB/s here).
    # Downloading natively and cutting in a second ffmpeg pass is dramatically
    # faster while keeping frame-accurate boundaries.
    source_template = str(job_dir / "source.%(ext)s")
    fetch_cmd = ytdlp_command() + [
        "--no-warnings", "--newline",
        "--merge-output-format", "mp4",
        "-f", fmt, "-o", source_template, url,
    ]

    # Stage 2: cut the segment.
    #
    # We re-encode rather than stream-copy. Stream copy is faster but only
    # guarantees correct boundaries when both ends land exactly on keyframes;
    # a sweep across random ranges showed copied clips could start late (SSIM
    # ~0.88 for fractional starts) and truncate the tail (SSIM ~0.70-0.90),
    # which would silently drop requested frames. Re-encoding keeps both
    # boundaries frame-accurate, so the requested range is always fully
    # contained. See NOTES.md for the measurements.
    clip_path = job_dir / "clip.mp4"

    with LOCK:
        JOBS[job_id] = {"status": "processing", "progress": "Starting…", "file": None, "error": None}

    def set_progress(message: str):
        with LOCK:
            JOBS[job_id]["progress"] = message

    def run(cmd, on_line=None):
        """Run a subprocess, streaming output lines to a callback."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        last_line = ""
        for line in proc.stdout:
            line = line.strip()
            if line:
                last_line = line[-400:]
                if on_line:
                    on_line(line)
        return proc.wait(), last_line

    def worker():
        try:
            set_progress("Downloading source media…")
            code, last = run(fetch_cmd, lambda line: set_progress(line))
            if code != 0:
                with LOCK:
                    JOBS[job_id]["status"] = "failed"
                    JOBS[job_id]["error"] = last or "Download failed. The requested resolution may not be available."
                    JOBS[job_id]["progress"] = "Failed"
                return

            sources = [p for p in job_dir.iterdir()
                       if p.is_file() and p.name.startswith("source.") and p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
            if not sources:
                with LOCK:
                    JOBS[job_id]["status"] = "failed"
                    JOBS[job_id]["error"] = "Downloaded media was not found."
                    JOBS[job_id]["progress"] = "Failed"
                return
            source = sources[0]

            # Trim exactly to the requested range. -ss before -i seeks to the
            # preceding keyframe and decodes forward, so the first output frame
            # is the requested start frame, and -t covers the requested span.
            cut_start = float(start_s)
            duration = float(end_s) - cut_start

            set_progress("Cutting the selected segment…")
            cut_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", format_seconds(cut_start),
                "-i", str(source),
                "-t", format_seconds(duration),
                # ultrafast keeps the cut ~4x faster than veryfast while still
                # decoding every frame, so nothing in the range is dropped.
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(clip_path),
            ]
            cut_code, cut_last = run(cut_cmd)
            if cut_code != 0 or not clip_path.exists():
                with LOCK:
                    JOBS[job_id]["status"] = "failed"
                    JOBS[job_id]["error"] = cut_last or "FFmpeg could not cut the segment."
                    JOBS[job_id]["progress"] = "Failed"
                return

            # The full source is large; keep only the finished clip.
            try:
                source.unlink()
            except OSError:
                pass

            with LOCK:
                JOBS[job_id]["status"] = "completed"
                JOBS[job_id]["file"] = str(clip_path)
                JOBS[job_id]["progress"] = "Ready to download"
        except Exception as exc:
            with LOCK:
                JOBS[job_id]["status"] = "failed"
                JOBS[job_id]["error"] = str(exc)
                JOBS[job_id]["progress"] = "Failed"
    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        return {"job_id": job_id, "status": job["status"], "progress": job["progress"], "error": job["error"],
                "download_url": f"/api/jobs/{job_id}/file" if job["status"] == "completed" else None}

@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str, background_tasks: BackgroundTasks):
    with LOCK:
        job = JOBS.get(job_id)
        if not job or job["status"] != "completed" or not job["file"]:
            raise HTTPException(404, "File is not ready.")
        path = Path(job["file"])
    if not path.exists():
        raise HTTPException(404, "The output file no longer exists.")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
