#!/usr/bin/env python3
"""vidgrab - download video/audio from URLs: single, batch, or full playlist.

Usage:
  python vidgrab.py <url>                    # best quality video
  python vidgrab.py <url> --audio            # extract as mp3
  python vidgrab.py <url> --quality 720      # cap video height
  python vidgrab.py <url> --out ~/Videos
  python vidgrab.py <url> --playlist         # every item in the playlist
  python vidgrab.py urls.txt --batch         # one URL per line in the file

Supports any site yt-dlp supports: youtube, tiktok, instagram, x/twitter,
facebook, vimeo, direct .mp4 links, etc.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def build_cmd(url, out, audio=False, quality=None, playlist=False, batch=False):
    cmd = ["yt-dlp", "-o", str(out / "%(playlist_index)s - %(title).100s.%(ext)s"
                           if playlist else "%(title).100s.%(ext)s")]
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
    if batch:
        cmd += ["-a", url]   # yt-dlp reads URLs from file, skips bad lines
    else:
        cmd.append(url)
    return cmd


def main():
    p = argparse.ArgumentParser(description="download video/audio from URL(s)")
    p.add_argument("url", help="a URL, or a .txt file of URLs with --batch")
    p.add_argument("--audio", action="store_true", help="extract as mp3")
    p.add_argument("--quality", default=None, help="max height, e.g. 720")
    p.add_argument("--out", default=".", help="output folder")
    p.add_argument("--playlist", action="store_true", help="download full playlist")
    p.add_argument("--batch", action="store_true", help="URL arg is a text file, one URL per line")
    args = p.parse_args()

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    if args.batch:
        src = Path(args.url).expanduser()
        if not src.is_file():
            sys.exit(f"batch file not found: {src}")
        cmd = build_cmd(str(src), out, args.audio, args.quality, args.playlist, True)
    else:
        cmd = build_cmd(args.url, out, args.audio, args.quality, args.playlist, False)

    try:
        subprocess.run(cmd, check=True)
        print("done ->", out)
    except FileNotFoundError:
        sys.exit("yt-dlp not installed: pip install yt-dlp (and ffmpeg for mp3)")
    except subprocess.CalledProcessError as e:
        sys.exit(f"download failed (exit {e.returncode})")


if __name__ == "__main__":
    main()
