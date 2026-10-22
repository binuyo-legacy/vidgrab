"""vidgrab web API: FastAPI wrapper around the vidgrab CLI.

Run locally from this folder:
    uvicorn main:app --reload --port 8000

Endpoints:
    POST /api/download          start a job, returns {"id", "status"}
    GET  /api/jobs/{id}         progress and status
    GET  /api/jobs/{id}/file    stream the finished file (deleted after it is served)
"""
import mimetypes
import os
import re
import shutil
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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

# vidgrab.py lives in the repo root, one level above this folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vidgrab import VidgrabError, build_cmd, read_resolved_format, run_yt_dlp, validate_url  # noqa: E402

MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
JOB_TTL = int(os.environ.get("JOB_TTL", "3600"))          # seconds before an unclaimed job is removed
DOWNLOAD_TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", "900"))
MAX_FILESIZE = os.environ.get("MAX_FILESIZE", "2G")        # server-side cap so one job can't fill the disk
ALLOWED_QUALITIES = {"best", "1080", "720", "480", "360"}
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if o.strip()
]

_PCT_RE = re.compile(r"\[download\]\s+([\d.]+)%")
_POST_RE = re.compile(r"^\[(Merger|ExtractAudio|VideoConvertor|Fixup\w*)\]")
_UNFINISHED = (".part", ".ytdl", ".temp")


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


app = FastAPI(title="vidgrab API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition"],
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
def start_download(req: DownloadRequest):
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
