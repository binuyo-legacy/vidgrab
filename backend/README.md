backend/ is the FastAPI wrapper around vidgrab.py.

Install and run locally from the repo root:

    pip install -r requirements.txt -r backend/requirements.txt
    cd backend
    uvicorn main:app --reload --port 8000

It needs ffmpeg on the PATH for mp3 downloads and for merging video and audio.

Config (environment variables):
    ALLOWED_ORIGINS    comma-separated websites allowed to call the API
                       (default: https://vidgrabv.netlify.app plus the local Vite dev server)
    YOUTUBE_COOKIES_B64  optional. a base64 cookies.txt from a logged-in YouTube browser session.
                       helps when YouTube asks for a "not a bot" sign-in check
    MAX_DURATION       longest YouTube video /api/stream will convert, in seconds (default 1800)
    MAX_CONCURRENT     simultaneous downloads (default 2)
    DOWNLOAD_TIMEOUT   seconds per download attempt (default 900)
    MAX_FILESIZE       per-file cap passed to yt-dlp (default 2G)
    JOB_TTL            seconds before an uncollected job is deleted (default 3600)

Streaming endpoint (used for direct YouTube downloads):

    GET /api/stream?url=<youtube link>&format=mp4&quality=720
    GET /api/stream?url=<youtube link>&format=mp3&quality=192

mp4 quality is a max height (best, 1080, 720, 480, 360). mp3 quality is the bitrate (128, 192, 320).
The server downloads and converts the file, then streams it back in one response, so the
browser never talks to YouTube. Limits: 5 requests per minute per IP, and MAX_CONCURRENT
conversions at once.
