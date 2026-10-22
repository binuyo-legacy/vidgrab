# VibeN (formerly vidgrab)

The web app was rebranded to VibeN. The CLI and repo name stay `vidgrab`.

A small command-line wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp) for downloading video or audio from a single URL, a playlist, or a list of URLs in a text file.

Works with any site yt-dlp supports, including YouTube, TikTok, Instagram, X/Twitter, Facebook, Vimeo, and direct .mp4 links.

## Install

Requires Python 3.8+.

1. Install yt-dlp:

       pip install -r requirements.txt

   (same as `pip install yt-dlp`)

2. Install ffmpeg. It is needed for mp3 extraction (`--audio`) and for merging separate video and audio streams.

   - macOS (Homebrew): `brew install ffmpeg`
   - Ubuntu / Debian: `sudo apt install ffmpeg`
   - Windows (winget): `winget install ffmpeg`

3. Clone this repo and run the script:

       git clone https://github.com/binuyo-legacy/vidgrab.git
       cd vidgrab
       python vidgrab.py <url>

## Usage

Single video, best quality:

    python vidgrab.py https://www.youtube.com/watch?v=dQw4w9WgXcQ

Extract audio as mp3:

    python vidgrab.py https://www.youtube.com/watch?v=dQw4w9WgXcQ --audio

Cap the resolution (here, 720p or lower):

    python vidgrab.py https://www.youtube.com/watch?v=dQw4w9WgXcQ --quality 720

Save somewhere specific:

    python vidgrab.py <url> --out ~/Videos

Download a full playlist (files are numbered: `1 - title.mp4`, `2 - title.mp4`, ...):

    python vidgrab.py "https://www.youtube.com/playlist?list=PLxxxx" --playlist

Batch download from a text file with one URL per line:

    python vidgrab.py urls.txt --batch

An example file is included as `urls.txt.example`. Copy it to `urls.txt` and edit it:

    https://www.youtube.com/watch?v=dQw4w9WgXcQ
    https://vimeo.com/76979871
    https://example.com/clip.mp4

Options can be combined, for example `python vidgrab.py urls.txt --batch --quality 720 --out ~/Videos`.

Limit long or large downloads, and control timeouts and retries:

    python vidgrab.py <url> --max-duration 3600        # skip anything longer than 1 hour
    python vidgrab.py <url> --max-filesize 500M        # skip files larger than 500 MB
    python vidgrab.py <url> --timeout 600 --retries 1  # 10 min per attempt, 1 retry

## Options

| Flag | Meaning |
| --- | --- |
| `url` | a URL, or a path to a .txt file with `--batch` |
| `--audio` | extract audio as mp3 (needs ffmpeg) |
| `--quality N` | cap video height, e.g. `720` |
| `--out DIR` | output folder (created if missing, default: current folder) |
| `--playlist` | download every item in a playlist |
| `--batch` | treat the argument as a text file of URLs, one per line |
| `--max-duration SECONDS` | skip videos longer than this (uses yt-dlp `--match-filter "duration < N"`, rejected before download) |
| `--max-filesize SIZE` | skip files larger than this, e.g. `500M` or `2G` (uses yt-dlp `--max-filesize`) |
| `--timeout SECONDS` | per-attempt download timeout (default `1800`) |
| `--retries N` | retries after a failed attempt, `0` to `5` (default `2`) |

## Errors

- Input is checked before anything runs. Only `http://` and `https://` URLs are accepted. Anything else exits with a message such as `error: not a valid URL: ...`.
- A missing `yt-dlp`, or a missing `ffmpeg` when `--audio` is used, exits with an install hint.
- Failed downloads are retried up to `--retries` times, with a short pause between attempts. Errors that will not change on retry (unsupported URL, private or unavailable video, HTTP 403/404) are not retried.
- A hung download is stopped after `--timeout` seconds and counts as a failed attempt.
- With `--batch`, a retry runs the whole batch file again. yt-dlp already skips bad lines and keeps going past them.


## Web app

The repo also has a web version: a FastAPI backend that wraps `vidgrab.py`, and a React + Vite frontend.

    vidgrab/
    ├── vidgrab.py        the CLI (the backend imports its download code)
    ├── backend/          FastAPI app (main.py), requirements.txt
    ├── frontend/         Vite + React app
    ├── Dockerfile        backend image with ffmpeg, for fly.io or railway
    └── netlify.toml      frontend build + SPA redirect for Netlify

The backend API:

- `POST /api/download` with `{"url", "mode": "video" | "audio", "quality": "best" | "1080" | "720" | "480" | "360", "max_duration"}` starts a job and returns `{"id", "status"}`.
- `GET /api/jobs/{id}` returns progress (`progress` 0 to 100, `message`, `status`).
- `GET /api/jobs/{id}/file` streams the finished file. The server deletes it once it has been sent.

Jobs run in background threads. `MAX_CONCURRENT` (default 2) limits simultaneous downloads. Files are written to a temp folder, and any job nobody collects within an hour is cleaned up.

### Local dev

Terminal 1, backend (needs yt-dlp and ffmpeg):

    pip install -r requirements.txt -r backend/requirements.txt
    cd backend
    uvicorn main:app --reload --port 8000

Terminal 2, frontend:

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173. Vite proxies `/api` to `http://localhost:8000`, so no environment variable is needed locally. To point the dev server at a different backend, set `VIDGRAB_API`.

### Deploy

The frontend and backend deploy separately, because the browser cannot run yt-dlp and Netlify cannot run long downloads.

Backend on fly.io or Railway, using the root `Dockerfile` (it installs ffmpeg). Build from the repo root. On fly.io, run `fly launch` in the repo root and let it detect the Dockerfile. Railway works the same way. Set these environment variables if you need to change the defaults:

- `ALLOWED_ORIGINS` the frontend origin, e.g. `https://your-site.netlify.app` (comma separated). Without it, the browser blocks API calls from any other origin.
- `MAX_CONCURRENT`, `DOWNLOAD_TIMEOUT`, `MAX_FILESIZE`, `JOB_TTL`.

Frontend on Netlify: `netlify.toml` already sets the build command, publish folder and the SPA redirect. Connect the repo, then set `VITE_API_URL` to your backend URL (for example `https://vidgrab-api.fly.dev`, no trailing slash) under Site settings, then Environment variables. Redeploy after changing it, because Vite bakes it in at build time.

Notes: the in-memory job store means the backend must run as a single instance. Do not scale it horizontally without moving jobs to shared storage.

## Notes

- Only download content you have the right to download. Follow the terms of the sites you use.
- If yt-dlp stops working on a site, update it: `pip install -U yt-dlp`.

## License

MIT. See [LICENSE](LICENSE).
