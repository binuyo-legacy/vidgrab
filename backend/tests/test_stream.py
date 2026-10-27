"""Tests for the streaming endpoint. Run from the backend folder:  python -m pytest tests -q

No real YouTube calls are made: yt-dlp is replaced with a stub that writes a small test file.
ffmpeg is real, so the merge and mp3 steps are actually exercised.
"""
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402

GOOD_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
ORIGIN = "https://vidgrabv.netlify.app"

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    main._rl_hits.clear()
    yield
    main._rl_hits.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def _make_media(path: Path, kind: str) -> None:
    if kind == "video":
        args = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest",
                "-c:v", "libx264", "-c:a", "aac", str(path)]
    elif kind == "video_only":
        args = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
                "-c:v", "libx264", str(path)]
    else:  # audio
        args = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(path)]
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def _streams(path: Path) -> set:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return set(out.split())


# --- URL whitelist -------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc",
    "https://youtu.be/abc",
    "https://m.youtube.com/watch?v=abc",
    "https://music.youtube.com/watch?v=abc",
])
def test_youtube_urls_accepted(url):
    assert main.validate_youtube_url(url) == url


@pytest.mark.parametrize("url", [
    "https://vimeo.com/76979871",
    "https://example.com/youtube.com",
    "https://youtube.com.evil.io/watch?v=abc",
    "ftp://youtube.com/x",
    "javascript:alert(1)",
    "",
])
def test_non_youtube_urls_rejected(url, client):
    r = client.get("/api/stream", params={"url": url, "format": "mp4"})
    assert r.status_code in (400, 422)


# --- filenames -----------------------------------------------------------------------------
def test_safe_title_strips_bad_characters():
    assert main.safe_title('My <video>: "best" / cut?') == "My video best cut"
    assert main.safe_title("   ") == "download"
    assert len(main.safe_title("x" * 500)) <= 120


def test_content_disposition_ascii_and_utf8():
    h = main.content_disposition("Ìjọ́sìn mp3.mp3")
    assert 'filename="jsn mp3.mp3"' in h
    assert "filename*=UTF-8''" in h
    assert "%C3%8C" in h  # the Yoruba title survives in the UTF-8 form
    assert main.content_disposition("hello.mp4").startswith('attachment; filename="hello.mp4"')


# --- CORS ----------------------------------------------------------------------------------
def test_cors_allows_whitelisted_origin(client):
    r = client.options("/api/stream", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == ORIGIN


def test_cors_blocks_other_origin(client):
    r = client.options("/api/stream", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_allowed_origins_read_from_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example, https://b.example ,")
    import importlib
    reloaded = importlib.reload(main)
    try:
        assert reloaded.ALLOWED_ORIGINS == ["https://a.example", "https://b.example"]
    finally:
        monkeypatch.delenv("ALLOWED_ORIGINS")
        importlib.reload(main)


# --- rate limiting -------------------------------------------------------------------------
def test_stream_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(main, "_check_tools", lambda fmt: None)
    monkeypatch.setattr(main, "_preflight_info", lambda url: (60.0, "t"))
    monkeypatch.setattr(main, "_fetch_mp4", lambda *a, **k: (_ for _ in ()).throw(main.StreamError("x")))
    headers = {"X-Forwarded-For": "203.0.113.7"}
    codes = [client.get("/api/stream", params={"url": GOOD_URL}, headers=headers).status_code for _ in range(6)]
    assert codes[:5] == [422] * 5          # counted, then fails inside the stub
    assert codes[5] == 429                 # sixth request from the same IP is refused


def test_rate_limit_is_per_ip(client, monkeypatch):
    monkeypatch.setattr(main, "_check_tools", lambda fmt: None)
    monkeypatch.setattr(main, "_preflight_info", lambda url: (60.0, "t"))
    monkeypatch.setattr(main, "_fetch_mp4", lambda *a, **k: (_ for _ in ()).throw(main.StreamError("x")))
    for _ in range(5):
        client.get("/api/stream", params={"url": GOOD_URL}, headers={"X-Forwarded-For": "1.1.1.1"})
    r = client.get("/api/stream", params={"url": GOOD_URL}, headers={"X-Forwarded-For": "2.2.2.2"})
    assert r.status_code == 422


# --- duration cap and preflight --------------------------------------------------------------
def test_too_long_video_refused(client, monkeypatch):
    monkeypatch.setattr(main, "_check_tools", lambda fmt: None)
    monkeypatch.setattr(main, "MAX_DURATION", 600)

    def fake_run(cmd, **kw):
        assert "--simulate" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="3600.0\tlong one\n", stderr="")
    monkeypatch.setattr(main.subprocess, "run", fake_run)
    r = client.get("/api/stream", params={"url": GOOD_URL, "format": "mp3"})
    assert r.status_code == 422
    assert "too long" in r.json()["detail"]


def test_preflight_failure_is_a_clear_message(client, monkeypatch):
    monkeypatch.setattr(main, "_check_tools", lambda fmt: None)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ERROR: Video unavailable")
    monkeypatch.setattr(main.subprocess, "run", fake_run)
    r = client.get("/api/stream", params={"url": GOOD_URL})
    assert r.status_code == 422
    assert "unavailable" in r.json()["detail"]


def test_bad_quality_rejected(client):
    assert client.get("/api/stream", params={"url": GOOD_URL, "format": "mp3", "quality": "999"}).status_code == 400
    assert client.get("/api/stream", params={"url": GOOD_URL, "format": "mp4", "quality": "4k"}).status_code == 400


# --- cookies -------------------------------------------------------------------------------
def test_cookies_from_env(monkeypatch):
    payload = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    monkeypatch.setenv("YOUTUBE_COOKIES_B64", base64.b64encode(payload).decode())
    monkeypatch.setattr(main, "_COOKIE_PATH", None)
    main._init_cookies()
    try:
        assert main._COOKIE_PATH and Path(main._COOKIE_PATH).read_bytes() == payload
        assert main._cookie_args() == ["--cookies", main._COOKIE_PATH]
        assert (os.stat(main._COOKIE_PATH).st_mode & 0o777) == 0o600
    finally:
        if main._COOKIE_PATH and os.path.exists(main._COOKIE_PATH):
            os.unlink(main._COOKIE_PATH)
        monkeypatch.setattr(main, "_COOKIE_PATH", None)


def test_no_cookies_when_env_missing(monkeypatch):
    monkeypatch.delenv("YOUTUBE_COOKIES_B64", raising=False)
    monkeypatch.setattr(main, "_COOKIE_PATH", None)
    main._init_cookies()
    assert main._cookie_args() == []


# --- real ffmpeg steps ---------------------------------------------------------------------
@needs_ffmpeg
def test_mp4_merge_puts_video_and_audio_in_one_file(tmp_path):
    v, a = tmp_path / "video.mp4", tmp_path / "audio.m4a"
    _make_media(v, "video_only")
    _make_media(a, "audio")
    out = tmp_path / "output.mp4"
    main._ffmpeg_mp4(out, v, a)
    assert _streams(out) == {"video", "audio"}


@needs_ffmpeg
def test_single_file_mp4_conversion(tmp_path):
    src = tmp_path / "single.mp4"
    _make_media(src, "video")
    out = tmp_path / "output.mp4"
    main._ffmpeg_mp4(out, src)
    assert _streams(out) == {"video", "audio"}


@needs_ffmpeg
def test_stream_mp4_end_to_end(client, monkeypatch):
    monkeypatch.setattr(main, "_check_tools", lambda fmt: None)
    monkeypatch.setattr(main, "_preflight_info", lambda url: (60.0, "Ìjọ́sìn: test/clip"))

    def fake_fetch(url, workdir, height):
        v, a = workdir / "video.mp4", workdir / "audio.m4a"
        _make_media(v, "video_only")
        _make_media(a, "audio")
        out = workdir / "output.mp4"
        main._ffmpeg_mp4(out, v, a)
        v.unlink(); a.unlink()
        return out
    monkeypatch.setattr(main, "_fetch_mp4", fake_fetch)

    r = client.get("/api/stream", params={"url": GOOD_URL, "format": "mp4", "quality": "720"},
                   headers={"Origin": ORIGIN})
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "attachment" in r.headers["content-disposition"]
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert int(r.headers["content-length"]) == len(r.content) > 1000
    assert r.content[4:8] == b"ftyp"  # real mp4 file


@needs_ffmpeg
def test_stream_mp3_end_to_end(client, monkeypatch):
    monkeypatch.setattr(main, "_check_tools", lambda fmt: None)
    monkeypatch.setattr(main, "_preflight_info", lambda url: (60.0, "sermon clip"))

    def fake_fetch(url, workdir, bitrate):
        out = workdir / "audio.mp3"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=1", "-b:a", f"{bitrate}k", str(out)], check=True)
        return out
    monkeypatch.setattr(main, "_fetch_mp3", fake_fetch)

    r = client.get("/api/stream", params={"url": GOOD_URL, "format": "mp3", "quality": "192"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["content-disposition"].startswith('attachment; filename="sermon clip.mp3"')
    assert len(r.content) > 1000


def test_temp_folder_removed_after_stream(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_check_tools", lambda fmt: None)
    monkeypatch.setattr(main, "_preflight_info", lambda url: (60.0, "t"))
    seen = {}

    def fake_fetch(url, workdir, bitrate):
        seen["dir"] = workdir
        out = workdir / "audio.mp3"
        out.write_bytes(b"ID3" + b"0" * 5000)
        return out
    monkeypatch.setattr(main, "_fetch_mp3", fake_fetch)
    r = client.get("/api/stream", params={"url": GOOD_URL, "format": "mp3"})
    assert r.status_code == 200 and len(r.content) == 5003
    assert not seen["dir"].exists()
