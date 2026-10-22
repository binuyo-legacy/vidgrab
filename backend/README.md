backend/ is the FastAPI wrapper around vidgrab.py.

Install and run locally from the repo root:

    pip install -r requirements.txt -r backend/requirements.txt
    cd backend
    uvicorn main:app --reload --port 8000

It needs ffmpeg on the PATH for mp3 downloads and for merging video and audio.

Config (environment variables, all optional):
    ALLOWED_ORIGINS    comma-separated CORS origins (default: local Vite dev server)
    MAX_CONCURRENT     simultaneous downloads (default 2)
    DOWNLOAD_TIMEOUT   seconds per download attempt (default 900)
    MAX_FILESIZE       per-file cap passed to yt-dlp (default 2G)
    JOB_TTL            seconds before an uncollected job is deleted (default 3600)
