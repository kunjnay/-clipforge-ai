"""Free, local-first link intake. Only public video URLs are attempted; no cookies or login bypass."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


def validate_source_url(raw: str) -> tuple[str, str]:
    value = (raw or "").strip()
    if len(value) > 2048:
        raise ValueError("Link is too long.")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a normal HTTPS YouTube video link.")
    host = parsed.hostname.lower().rstrip(".")
    path = parsed.path or "/"
    if host in YOUTUBE_HOSTS:
        if host.endswith("youtu.be"):
            if len(path.strip("/")) < 6:
                raise ValueError("That does not look like a valid YouTube video link.")
        elif not (path.startswith("/watch") or path.startswith("/shorts/") or path.startswith("/live/") or path.startswith("/embed/")):
            raise ValueError("Paste a YouTube video, Shorts, or live-replay link—not a channel or playlist.")
        return "youtube", value
    raise ValueError("Only YouTube video links are supported as a source.")


def download_public_video(url: str, job_id: str, destination_dir: Path) -> Path:
    """Download a public/authorised video using yt-dlp. Never loads browser cookies."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is missing. Install the project requirements and restart ClipForge.") from exc

    destination_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(destination_dir / f"{job_id}.%(ext)s")
    max_mb = max(50, min(4096, int(os.getenv("MAX_SOURCE_MB", "2048"))))
    options = {
        "outtmpl": output_template,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": max_mb * 1024 * 1024,
        "overwrites": False,
        "cachedir": False,
        "no_check_certificates": False,
        # Deliberately do not pass cookies, browser profiles, or authentication data.
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("The source did not return a downloadable video.")
            prepared = Path(ydl.prepare_filename(info))
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:700]
        if "login" in message.lower() or "private" in message.lower() or "authentication" in message.lower():
            raise RuntimeError("This link is private or login-gated. ClipForge only processes publicly accessible links; it does not use account cookies or bypass access controls.") from exc
        raise RuntimeError(f"Could not download this public video: {message}") from exc

    candidates = [destination_dir / f"{job_id}.mp4", prepared]
    candidates += sorted(destination_dir.glob(f"{job_id}.*"))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"} and candidate.stat().st_size > 0:
            return candidate
    raise RuntimeError("The downloader finished but no playable video file was produced. Try updating yt-dlp.")
