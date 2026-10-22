# vidgrab

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

## Options

| Flag | Meaning |
| --- | --- |
| `url` | a URL, or a path to a .txt file with `--batch` |
| `--audio` | extract audio as mp3 (needs ffmpeg) |
| `--quality N` | cap video height, e.g. `720` |
| `--out DIR` | output folder (created if missing, default: current folder) |
| `--playlist` | download every item in a playlist |
| `--batch` | treat the argument as a text file of URLs, one per line |

## Notes

- Only download content you have the right to download. Follow the terms of the sites you use.
- If yt-dlp stops working on a site, update it: `pip install -U yt-dlp`.

## License

MIT. See [LICENSE](LICENSE).
