"""vidgrab web API: FastAPI wrapper around the vidgrab CLI.

Run locally from this folder:
    uvicorn main:app --reload --port 8000

Endpoints:
    POST /api/download            start a job, returns {"id", "status"}   (any yt-dlp site)
    GET  /api/jobs/{id}           progress and status
    GET  /api/jobs/{id}/file      stream the finished file (deleted after it is served)
    GET  /api/stream?url=&format= convert a link and stream the file straight back
                                  in one request (mp3 or mp4). any yt-dlp site.
"""
import atexit
import base64
import collections
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

# vidgrab.py lives in the repo root, one level above this folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vidgrab import VidgrabError, build_cmd, read_resolved_format, run_yt_dlp, validate_url  # noqa: E402

MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
JOB_TTL = int(os.environ.get("JOB_TTL", "3600"))          # seconds before an unclaimed job is removed
DOWNLOAD_TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", "900"))
MAX_FILESIZE = os.environ.get("MAX_FILESIZE", "2G")        # server-side cap so one job can't fill the disk
MAX_DURATION = int(os.environ.get("MAX_DURATION", "1800"))  # seconds; longer YouTube videos are refused by /api/stream
ALLOWED_QUALITIES = {"best", "1080", "720", "480", "360"}
MP3_BITRATES = {"128", "192", "320"}
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://vidgrabv.netlify.app,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",") if o.strip()
]
STREAM_CHUNK = 64 * 1024
RATE_WINDOW = 60                                            # seconds
RATE_LIMITS = {"stream": 5, "download": 10}                 # requests per IP per window

_PCT_RE = re.compile(r"\[download\]\s+([\d.]+)%")
_POST_RE = re.compile(r"^\[(Merger|ExtractAudio|VideoConvertor|Fixup\w*)\]")
_UNFINISHED = (".part", ".ytdl", ".temp")

log = logging.getLogger("vidgrab")


# --- YouTube cookies (optional, helps with YouTube's bot check) -------------------------
_COOKIE_PATH: Optional[str] = None


def _init_cookies() -> None:
    """Decode YOUTUBE_COOKIES_B64 (a base64 cookies.txt in Netscape format) to a private temp file."""
    global _COOKIE_PATH
    b64 = os.environ.get("YOUTUBE_COOKIES_B64", "").strip()
    if not b64:
        return
    try:
        data = base64.b64decode(b64)
    except Exception:
        log.error("YOUTUBE_COOKIES_B64 is not valid base64, cookies ignored")
        return
    fd, path = tempfile.mkstemp(prefix="vidgrab-cookies-", suffix=".txt")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(path, 0o600)
    _COOKIE_PATH = path
    atexit.register(lambda: os.path.exists(path) and os.unlink(path))


def _cookie_args() -> list:
    return ["--cookies", _COOKIE_PATH] if _COOKIE_PATH else []


_init_cookies()


# --- per-IP rate limiting ---------------------------------------------------------------
_rl_lock = threading.Lock()
_rl_hits: dict = {}


def _client_ip(request: Request) -> str:
    # Render (and most hosts) put a proxy in front, so the real client is the first X-Forwarded-For entry.
    xff = request.headers.get("x-forwarded-for", "")
    if xff.strip():
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate(request: Request, bucket: str) -> None:
    key = f"{bucket}:{_client_ip(request)}"
    now = time.time()
    with _rl_lock:
        if len(_rl_hits) > 5000:  # keep memory bounded
            for k in [k for k, d in _rl_hits.items() if not d or now - d[-1] >= RATE_WINDOW]:
                _rl_hits.pop(k, None)
        dq = _rl_hits.setdefault(key, collections.deque())
        while dq and now - dq[0] >= RATE_WINDOW:
            dq.popleft()
        if len(dq) >= RATE_LIMITS[bucket]:
            raise HTTPException(
                status_code=429,
                detail="too many downloads from your connection. try again in a minute.",
                headers={"Retry-After": str(RATE_WINDOW)},
            )
        dq.append(now)


# --- helpers ----------------------------------------------------------------------------
class StreamError(Exception):
    """A user-facing failure while converting. The message is safe to show as-is."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


def validate_media_url(url: str) -> str:
    """Basic sanity check only: an http(s) link with a host. Any site yt-dlp supports is allowed through."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="that doesn't look like a full link. paste a full http or https link.")
    return url


_BAD_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def safe_title(title: str) -> str:
    t = _BAD_FILENAME_CHARS.sub("", title or "")
    t = re.sub(r"\s+", " ", t).strip(" .")[:120].strip(" .")
    return t or "download"


def content_disposition(filename: str) -> str:
    """attachment header with an ASCII fallback plus the full UTF-8 name (for non-English titles)."""
    ext = os.path.splitext(filename)[1]
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "").strip()
    if not ascii_name or ascii_name.startswith("."):
        ascii_name = f"download{ext}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _short_error(stderr: str) -> str:
    lines = [ln.strip() for ln in (stderr or "").splitlines() if "ERROR" in ln]
    msg = (lines[-1] if lines else "").split("ERROR:", 1)[-1].strip()
    return msg[:300]


def _friendly(stderr: str) -> str:
    low = (stderr or "").lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return "youtube asked for a sign-in check for this video. the server needs fresh YouTube cookies (YOUTUBE_COOKIES_B64)."
    if "private video" in low:
        return "this video is private."
    if "unsupported url" in low:
        return "this link is not from a site the downloader supports. check the link and try again."
    if "video unavailable" in low or "this video is not available" in low:
        return "this video is unavailable."
    if "age" in low and "restricted" in low:
        return "this video is age restricted and can't be downloaded without a signed-in account."
    detail = _short_error(stderr)
    return f"the video could not be downloaded: {detail}" if detail else "the video could not be downloaded."


def _check_tools(fmt: str) -> None:
    if shutil.which("yt-dlp") is None:
        raise HTTPException(status_code=503, detail="server is missing yt-dlp, contact the operator")
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="server is missing ffmpeg, the download is unavailable")


def _ytdlp_base(workdir: Path) -> list:
    return [
        "yt-dlp", "--no-playlist", "--no-warnings", "--quiet", "--no-part",
        "--socket-timeout", "15", "--max-filesize", MAX_FILESIZE,
        "--ffmpeg-location", shutil.which("ffmpeg") or "ffmpeg",
        *_cookie_args(),
    ]


def _run(cmd: list, timeout: int = DOWNLOAD_TIMEOUT) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise StreamError("the download took too long and was stopped. try again.")
    if r.returncode != 0:
        raise StreamError(_friendly(r.stderr), stderr=r.stderr)


def _preflight_info(url: str):
    """yt-dlp --simulate: checks the video is reachable and reads its length and title. Downloads nothing."""
    cmd = [
        "yt-dlp", "--simulate", "--no-playlist", "--no-warnings", "--quiet",
        "--socket-timeout", "15", "--print", "%(duration)s\t%(title)s",
        *_cookie_args(), url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=45)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="checking the video timed out. try again.")
    if r.returncode != 0:
        log.warning("preflight failed: %s", (r.stderr or "")[:600])
        raise HTTPException(status_code=422, detail=_friendly(r.stderr))
    line = next((ln for ln in r.stdout.splitlines() if "\t" in ln), "")
    dur_s, _, title = line.partition("\t")
    duration = float(dur_s) if re.fullmatch(r"\d+(\.\d+)?", dur_s.strip()) else None
    if duration is None:
        raise HTTPException(status_code=422, detail="couldn't tell how long this video is, so it can't be downloaded here (live streams are not supported).")
    if duration > MAX_DURATION:
        raise HTTPException(status_code=422, detail=f"this video is too long ({int(duration) // 60} min). the limit is {MAX_DURATION // 60} min.")
    return duration, title.strip()


def _fetch_mp3(url: str, workdir: Path, bitrate: str) -> Path:
    """yt-dlp extracts the audio and converts it to mp3 (ffmpeg runs under yt-dlp's extract-audio step)."""
    cmd = _ytdlp_base(workdir) + [
        "-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", f"{bitrate}K",
        "-o", str(workdir / "audio.%(ext)s"), url,
    ]
    _run(cmd)
    found = [p for p in workdir.glob("audio.*") if p.suffix.lower() == ".mp3"]
    if not found:
        raise StreamError("the mp3 conversion produced no file. try again.")
    return found[0]


def _ffmpeg_mp4(out: Path, video: Path, audio: Optional[Path] = None) -> None:
    """Explicit ffmpeg step: copy the video stream as-is, put audio in AAC, write an mp4 with fast-start."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    if audio is not None:
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=DOWNLOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise StreamError("merging the video took too long and was stopped. try again.")
    if r.returncode != 0 or not out.exists():
        log.error("ffmpeg merge failed: %s", (r.stderr or "")[:600])
        raise StreamError("merging the video and audio failed. try again or pick a lower quality.")


def _fetch_mp4(url: str, workdir: Path, height: Optional[str]) -> Path:
    """Download the video and audio streams separately, then merge them with an explicit ffmpeg call."""
    cap = f"[height<={height}]" if height else ""
    base = _ytdlp_base(workdir)
    try:
        _run(base + ["-f", f"bv*{cap}", "-o", str(workdir / "video.%(ext)s"), url])
        _run(base + ["-f", "ba[ext=m4a]/ba", "-o", str(workdir / "audio.%(ext)s"), url])
        vids = [p for p in workdir.glob("video.*") if not p.name.endswith(_UNFINISHED)]
        auds = [p for p in workdir.glob("audio.*") if not p.name.endswith(_UNFINISHED)]
        if not vids or not auds:
            raise StreamError("the download produced no video or audio file. try again.")
        out = workdir / "output.mp4"
        _ffmpeg_mp4(out, vids[0], auds[0])
        for p in vids + auds:
            p.unlink(missing_ok=True)
        return out
    except StreamError as e:
        if "Requested format is not available" not in e.stderr:
            raise
    # Some sources only offer one file with video and audio together. Use that and convert it to mp4.
    _run(base + ["-f", f"b{cap}/b", "-o", str(workdir / "single.%(ext)s"), url])
    singles = [p for p in workdir.glob("single.*") if not p.name.endswith(_UNFINISHED)]
    if not singles:
        raise StreamError("the download produced no file. try again.")
    out = workdir / "output.mp4"
    _ffmpeg_mp4(out, singles[0])
    singles[0].unlink(missing_ok=True)
    return out


_stream_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class _Lease:
    """Holds one of the MAX_CONCURRENT stream slots and the temp folder until the file is sent. Releases once."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._lock = threading.Lock()
        self._done = False

    def release(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        shutil.rmtree(self.workdir, ignore_errors=True)
        _stream_slots.release()


def _file_chunks(path: Path, lease: _Lease):
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(STREAM_CHUNK)
                if not chunk:
                    break
                yield chunk
    finally:
        lease.release()


# --- jobs (existing flow, used by the web UI) -------------------------------------------
@dataclass
class Job:
    id: str
    mode: str
    url: str
    status: str = "queued"          # queued | running | done | error
    progress: float = 0.0
    message: str = "waiting for a free slot"
    error: Optional[str] = None
    workdir: Optional[Path] = None
    filename: Optional[str] = None
    filepath: Optional[Path] = None
    resolved_height: Optional[int] = None   # height yt-dlp actually downloaded (video only)
    resolved_format: Optional[str] = None   # yt-dlp format_id actually downloaded
    requested_quality: str = "best"
    created: float = field(default_factory=time.time)
    served: bool = False


jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="vidgrab")


def _remove_job(job: Job) -> None:
    if job.workdir and job.workdir.exists():
        shutil.rmtree(job.workdir, ignore_errors=True)
    with jobs_lock:
        jobs.pop(job.id, None)


def _sweep_expired() -> None:
    """Drop jobs nobody collected within JOB_TTL, along with their files."""
    cutoff = time.time() - JOB_TTL
    with jobs_lock:
        stale = [j for j in jobs.values() if j.created < cutoff]
    for job in stale:
        _remove_job(job)


def _janitor() -> None:
    while True:
        time.sleep(60)
        try:
            _sweep_expired()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_janitor, daemon=True).start()
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="vidgrab API", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition", "Content-Length"],
)


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    mode: Literal["video", "audio"] = "video"
    quality: str = "best"
    max_duration: Optional[int] = Field(default=None, ge=1, le=86400)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        try:
            return validate_url(v)
        except VidgrabError as e:
            raise ValueError(str(e))

    @field_validator("quality")
    @classmethod
    def _check_quality(cls, v: str) -> str:
        if v not in ALLOWED_QUALITIES:
            raise ValueError(f"quality must be one of: {', '.join(sorted(ALLOWED_QUALITIES))}")
        return v


def _find_output(workdir: Path) -> Optional[Path]:
    candidates = [
        p for p in workdir.iterdir()
        if p.is_file() and not p.name.startswith(".") and not p.name.endswith(_UNFINISHED)
    ]
    # Skip hidden files (the metadata sidecar) and unfinished downloads.
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def _on_line(job: Job, line: str) -> None:
    m = _PCT_RE.search(line)
    if m:
        pct = float(m.group(1))
        with jobs_lock:
            # Video+audio downloads report progress twice; never let the bar go backwards.
            job.progress = max(job.progress, min(pct, 98.0))
            job.message = f"downloading... {pct:.0f}%"
        return
    if _POST_RE.match(line):
        with jobs_lock:
            job.progress = max(job.progress, 98.0)
            job.message = "converting..." if "ExtractAudio" in line else "merging streams..."


def _run_job(job: Job, req: DownloadRequest) -> None:
    with jobs_lock:
        job.status = "running"
        job.message = "starting download"
    try:
        meta_path = job.workdir / ".vgmeta"
        cmd = build_cmd(
            req.url, job.workdir,
            audio=(req.mode == "audio"),
            quality=None if req.quality == "best" else req.quality,
            playlist=False, batch=False,
            max_duration=req.max_duration,
            max_filesize=MAX_FILESIZE,
            meta_path=meta_path,
        )
        cmd[1:1] = _cookie_args()   # cookies go before the URL, which is the last argument
        run_yt_dlp(
            cmd,
            timeout=DOWNLOAD_TIMEOUT,
            retries=2,
            on_line=lambda line: _on_line(job, line),
            log=lambda msg: _set_message(job, msg),
        )
        out = _find_output(job.workdir)
        if out is None:
            raise VidgrabError("the download finished but no file was produced")
        height, fmt = read_resolved_format(meta_path)
        with jobs_lock:
            job.filepath = out
            job.filename = out.name
            job.resolved_height = height
            job.resolved_format = fmt
            job.progress = 100.0
            job.status = "done"
            job.message = "ready"
    except VidgrabError as e:
        with jobs_lock:
            job.status = "error"
            job.error = str(e)
            job.message = "failed"
        shutil.rmtree(job.workdir, ignore_errors=True)
    except Exception:
        with jobs_lock:
            job.status = "error"
            job.error = "something went wrong on the server, please try again"
            job.message = "failed"
        shutil.rmtree(job.workdir, ignore_errors=True)


def _set_message(job: Job, msg: str) -> None:
    with jobs_lock:
        job.message = msg


def _preflight(req: DownloadRequest) -> None:
    if shutil.which("yt-dlp") is None:
        raise HTTPException(status_code=503, detail="server is missing yt-dlp, contact the operator")
    if req.mode == "audio" and shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="server is missing ffmpeg, mp3 is unavailable")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/download", status_code=202)
def start_download(req: DownloadRequest, request: Request):
    check_rate(request, "download")
    _preflight(req)
    _sweep_expired()
    job = Job(id=uuid.uuid4().hex, mode=req.mode, url=req.url, requested_quality=req.quality)
    job.workdir = Path(tempfile.mkdtemp(prefix=f"vidgrab-{job.id[:8]}-"))
    with jobs_lock:
        jobs[job.id] = job
    executor.submit(_run_job, job, req)
    return {"id": job.id, "status": job.status}


def _quality_label(job: Job) -> Optional[str]:
    """Human label for the quality actually delivered, e.g. "720p" or "mp3"."""
    if job.mode == "audio":
        return "mp3"
    if job.resolved_height:
        return f"{job.resolved_height}p"
    return None


def _get_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found or expired")
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _get_job(job_id)
    with jobs_lock:
        return {
            "id": job.id,
            "status": job.status,
            "mode": job.mode,
            "progress": round(job.progress, 1),
            "message": job.message,
            "error": job.error,
            "filename": job.filename,
            "requested_quality": job.requested_quality,
            "resolved_height": job.resolved_height,
            "quality_label": _quality_label(job),
            "ready": job.status == "done" and not job.served,
        }


@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str):
    job = _get_job(job_id)
    with jobs_lock:
        if job.status != "done" or job.filepath is None:
            raise HTTPException(status_code=409, detail="file is not ready yet")
        if job.served:
            raise HTTPException(status_code=410, detail="this file was already downloaded, start a new one")
        path = job.filepath
        filename = job.filename
        job.served = True
    if not path.exists():
        _remove_job(job)
        raise HTTPException(status_code=410, detail="file is no longer available, start a new one")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    # Files are removed once the response has been sent.
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        background=BackgroundTask(_remove_job, job),
    )


@app.get("/api/stream")
def stream_download(
    request: Request,
    url: str = Query(..., min_length=1, max_length=2048),
    format: Literal["mp3", "mp4"] = "mp4",
    quality: Optional[str] = None,
):
    """Convert a YouTube link on the server and stream the finished file back in one request.

    mp4: quality is a max height (best, 1080, 720, 480, 360; default 720).
    mp3: quality is the bitrate in kbps (128, 192, 320; default 192).
    """
    check_rate(request, "stream")
    url = validate_media_url(url)
    if format == "mp3":
        quality = quality or "192"
        if quality not in MP3_BITRATES:
            raise HTTPException(status_code=400, detail=f"mp3 quality must be one of: {', '.join(sorted(MP3_BITRATES))}")
    else:
        quality = quality or "720"
        if quality not in ALLOWED_QUALITIES:
            raise HTTPException(status_code=400, detail=f"mp4 quality must be one of: {', '.join(sorted(ALLOWED_QUALITIES))}")

    _check_tools(format)
    if not _stream_slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="the server is busy with other downloads. try again in a minute.")

    workdir = Path(tempfile.mkdtemp(prefix="vidgrab-stream-"))
    lease = _Lease(workdir)
    try:
        _duration, title = _preflight_info(url)
        if format == "mp3":
            out = _fetch_mp3(url, workdir, quality)
        else:
            out = _fetch_mp4(url, workdir, None if quality == "best" else quality)
        size = out.stat().st_size
    except HTTPException:
        lease.release()
        raise
    except StreamError as e:
        log.error("stream failed for %s: %s", url, e.stderr[:600])
        lease.release()
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        log.exception("stream crashed for %s", url)
        lease.release()
        raise HTTPException(status_code=500, detail="something went wrong on the server, please try again")

    filename = f"{safe_title(title)}.{format}"
    media_type = "audio/mpeg" if format == "mp3" else "video/mp4"
    return StreamingResponse(
        _file_chunks(out, lease),
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(size),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(lease.release),  # safety net if the client disconnects early
    )
