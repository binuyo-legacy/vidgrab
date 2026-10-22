#!/usr/bin/env python3
"""vidgrab - download video/audio from URLs: single, batch, or full playlist.

Usage:
  python vidgrab.py <url>                    # best quality video
  python vidgrab.py <url> --audio            # extract as mp3
  python vidgrab.py <url> --quality 720      # cap video height
  python vidgrab.py <url> --out ~/Videos
  python vidgrab.py <url> --playlist         # every item in the playlist
  python vidgrab.py urls.txt --batch         # one URL per line in the file
  python vidgrab.py <url> --max-duration 3600 --max-filesize 500M
  python vidgrab.py <url> --timeout 600 --retries 1

Supports any site yt-dlp supports: youtube, tiktok, instagram, x/twitter,
facebook, vimeo, direct .mp4 links, etc.
"""
import argparse
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 1800   # seconds per download attempt
DEFAULT_RETRIES = 2      # retries after the first failed attempt
RETRY_DELAY = 3          # seconds to wait between attempts

# yt-dlp errors that will not get better on a retry.
FATAL_PATTERNS = (
    "Unsupported URL",
    "Private video",
    "Video unavailable",
    "HTTP Error 404",
    "HTTP Error 403",
)

_FILESIZE_RE = re.compile(r"^\d+(\.\d+)?[KMGkmg]?$")


class VidgrabError(Exception):
    """A user-facing error. The message is safe to show as-is."""


def validate_url(url):
    """Return the stripped URL, or raise VidgrabError if it is not http(s)."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise VidgrabError(
            f"not a valid URL: {url!r} (it must start with http:// or https://)"
        )
    if not parsed.netloc:
        raise VidgrabError(f"not a valid URL: {url!r} (no host name after the scheme)")
    return url


def validate_filesize(value):
    """Accept yt-dlp size strings like 500M, 2G, 1.5G, or a plain byte count."""
    if value is None:
        return None
    value = str(value).strip()
    if not _FILESIZE_RE.match(value):
        raise VidgrabError(
            f"invalid --max-filesize {value!r}: use a size like 500M, 2G, or 1.5G"
        )
    return value


def build_cmd(url, out, audio=False, quality=None, playlist=False, batch=False,
              max_duration=None, max_filesize=None):
    template = ("%(playlist_index)s - " if playlist else "") + "%(title).100s.%(ext)s"
    cmd = ["yt-dlp", "-o", str(out / template)]
    if playlist:
        cmd.append("--yes-playlist")
    else:
        cmd.append("--no-playlist")
    if audio:
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    elif quality:
        cmd += ["-f", f"best[height<={quality}]/best"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best"]
    if max_duration:
        cmd += ["--match-filter", f"duration < {int(max_duration)}"]
    if max_filesize:
        cmd += ["--max-filesize", str(max_filesize)]
    if batch:
        cmd += ["-a", url]   # yt-dlp reads URLs from file, skips bad lines
    else:
        cmd.append(url)
    return cmd


def _run_once(cmd, timeout, on_line):
    """Run yt-dlp once. Returns (reason, fatal). reason is None on success."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding="utf-8", errors="replace",
    )
    state = {"fatal": False}

    def _pump():
        # Reads output in its own thread so a hung child (ffmpeg, a helper
        # process) holding the pipe open cannot block the timeout below.
        for line in proc.stdout:
            if any(p in line for p in FATAL_PATTERNS):
                state["fatal"] = True
            if on_line:
                on_line(line)
            else:
                print(line, end="", flush=True)

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    try:
        rc = proc.wait(timeout=timeout) if timeout else proc.wait()
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        reader.join(timeout=5)
        return f"timed out after {timeout}s", False
    reader.join(timeout=5)
    if rc != 0:
        return f"exit {rc}", state["fatal"]
    return None, False


def run_yt_dlp(cmd, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES,
               on_line=None, retry_delay=RETRY_DELAY, log=None):
    """Run yt-dlp with a per-attempt timeout and up to `retries` retries.

    Raises VidgrabError when every attempt fails. A fatal error (bad URL,
    private or unavailable video) is not retried.
    """
    if log is None:
        def log(msg):
            print(msg, file=sys.stderr, flush=True)
    attempts = retries + 1
    reason = None
    for attempt in range(1, attempts + 1):
        try:
            reason, fatal = _run_once(cmd, timeout, on_line)
        except FileNotFoundError:
            raise VidgrabError(
                "yt-dlp not installed: pip install yt-dlp (and ffmpeg for mp3)"
            )
        if reason is None:
            return
        if fatal:
            raise VidgrabError(f"download failed: {reason} (not retrying)")
        if attempt < attempts:
            log(f"attempt {attempt} failed ({reason}), retrying in {retry_delay}s...")
            time.sleep(retry_delay)
    raise VidgrabError(f"download failed after {attempts} attempt(s): {reason}")


def _positive_int(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if n <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return n


def _retries(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if not 0 <= n <= 5:
        raise argparse.ArgumentTypeError("must be between 0 and 5")
    return n


def main():
    p = argparse.ArgumentParser(description="download video/audio from URL(s)")
    p.add_argument("url", help="a URL, or a .txt file of URLs with --batch")
    p.add_argument("--audio", action="store_true", help="extract as mp3")
    p.add_argument("--quality", default=None, help="max height, e.g. 720")
    p.add_argument("--out", default=".", help="output folder")
    p.add_argument("--playlist", action="store_true", help="download full playlist")
    p.add_argument("--batch", action="store_true", help="URL arg is a text file, one URL per line")
    p.add_argument("--max-duration", type=_positive_int, default=None, metavar="SECONDS",
                   help="skip videos longer than this many seconds")
    p.add_argument("--max-filesize", default=None, metavar="SIZE",
                   help="skip files larger than this, e.g. 500M or 2G")
    p.add_argument("--timeout", type=_positive_int, default=DEFAULT_TIMEOUT, metavar="SECONDS",
                   help=f"per-attempt download timeout (default {DEFAULT_TIMEOUT})")
    p.add_argument("--retries", type=_retries, default=DEFAULT_RETRIES, metavar="N",
                   help=f"retries after a failed attempt, 0 to 5 (default {DEFAULT_RETRIES})")
    args = p.parse_args()

    try:
        max_filesize = validate_filesize(args.max_filesize)

        if shutil.which("yt-dlp") is None:
            raise VidgrabError("yt-dlp not installed: pip install yt-dlp (and ffmpeg for mp3)")
        if args.audio and shutil.which("ffmpeg") is None:
            raise VidgrabError("ffmpeg not found: it is required for --audio (mp3). "
                               "Install it, then run again.")

        out = Path(args.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)

        if args.batch:
            src = Path(args.url).expanduser()
            if not src.is_file():
                raise VidgrabError(f"batch file not found: {src}")
            cmd = build_cmd(str(src), out, args.audio, args.quality, args.playlist, True,
                            args.max_duration, max_filesize)
        else:
            url = validate_url(args.url)
            cmd = build_cmd(url, out, args.audio, args.quality, args.playlist, False,
                            args.max_duration, max_filesize)

        run_yt_dlp(cmd, timeout=args.timeout, retries=args.retries)
        print("done ->", out)
    except VidgrabError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
