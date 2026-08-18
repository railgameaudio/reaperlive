"""Get audio into the project: YouTube (or anything yt-dlp handles) or a local file."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


@dataclass
class SourceAudio:
    path: Path
    title: str
    duration: float
    origin: str


def is_url(source: str) -> bool:
    return bool(_URL_RE.match(source.strip()))


def _require(tool: str) -> str:
    found = shutil.which(tool)
    if not found:
        raise RuntimeError(
            f"{tool!r} not found on PATH. Install it and try again "
            f"(macOS: brew install {tool}, Debian/Ubuntu: sudo apt install {tool})."
        )
    return found


def probe_duration(path: Path) -> float:
    """Length in seconds, via ffprobe."""
    out = subprocess.run(
        [
            _require("ffprobe"), "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def slugify(text: str, fallback: str = "song") -> str:
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-.")
    return (slug[:80] or fallback).lower()


def download(url: str, workdir: Path) -> tuple[Path, str]:
    """Pull the best available audio stream down with yt-dlp."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError("yt-dlp is required to download URLs: pip install yt-dlp") from exc

    workdir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "download.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
    }
    log.info("Downloading %s", url)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info.get("_type") == "playlist":  # noplaylist should prevent this
        info = info["entries"][0]
    downloaded = Path(ydl.prepare_filename(info))
    if not downloaded.exists():
        matches = sorted(workdir.glob("download.*"))
        if not matches:
            raise RuntimeError(f"yt-dlp reported success but produced no file in {workdir}")
        downloaded = matches[0]
    return downloaded, info.get("title") or downloaded.stem


def to_wav(src: Path, dest: Path, sample_rate: int = 44100, channels: int = 2) -> Path:
    """Decode anything ffmpeg understands into a plain 16-bit PCM wav."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _require("ffmpeg"), "-y", "-loglevel", "error", "-i", str(src),
        "-vn", "-ac", str(channels), "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest


def acquire(source: str, workdir: Path, sample_rate: int = 44100,
            name: Optional[str] = None) -> SourceAudio:
    """Normalise ``source`` (URL or path) into a wav we can analyse and separate."""
    workdir.mkdir(parents=True, exist_ok=True)
    if is_url(source):
        raw, title = download(source, workdir)
        origin = source
    else:
        raw = Path(source).expanduser().resolve()
        if not raw.exists():
            raise FileNotFoundError(f"No such audio file: {raw}")
        title = raw.stem
        origin = str(raw)

    title = name or title
    wav = workdir / "mix.wav"
    if raw.suffix.lower() == ".wav" and raw != wav:
        # Still re-encode: guarantees sample rate, channel count and PCM format.
        to_wav(raw, wav, sample_rate)
    else:
        to_wav(raw, wav, sample_rate)
    duration = probe_duration(wav)
    log.info("Source ready: %s (%.1fs)", title, duration)
    return SourceAudio(path=wav, title=title, duration=duration, origin=origin)
