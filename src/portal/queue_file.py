"""The queue the helper keeps at the repository root."""

from __future__ import annotations

import fcntl
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def youtube_url(value: str) -> str | None:
    """One canonical watch URL per video, so repeats compare equal."""
    text = value.strip()
    if not text or len(text) > 2000:
        return None
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _YOUTUBE_HOSTS:
        return None
    ident = _video_id(parsed)
    if ident:
        return f"https://www.youtube.com/watch?v={ident}"
    if not parsed.path and host not in {"youtu.be", "www.youtu.be"}:
        return None
    return urlunparse(parsed._replace(fragment=""))


def _video_id(parsed) -> str | None:
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        ident = parsed.path.strip("/").split("/")[0]
        return ident if _VIDEO_ID.fullmatch(ident or "") else None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) > 1 and parts[0] in {"shorts", "embed", "live", "v"}:
        return parts[1] if _VIDEO_ID.fullmatch(parts[1]) else None
    found = parse_qs(parsed.query).get("v", [""])[0]
    return found if _VIDEO_ID.fullmatch(found) else None


def read_queue(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def append_queue(path: Path, url: str) -> bool:
    """Append the URL. Return False when that video is already in the file."""
    stored = youtube_url(url) or url.strip()
    if not stored:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = {
                youtube_url(line) or line.strip()
                for line in handle.read().splitlines()
                if line.strip()
            }
            if stored in existing:
                return False
            handle.seek(0, 2)
            handle.write(stored + "\n")
            handle.flush()
            return True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
