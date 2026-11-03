"""The queue the helper keeps at the repository root."""

from __future__ import annotations

import fcntl
import os
import re
import time
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


def parse_queue_line(raw: str) -> tuple[str, str, str] | None:
    """One queue line is a URL, an optional title, and an optional lookup state.

    ``failed`` in the third field means the title lookup ended without a title.
    """
    line = raw.strip()
    if not line:
        return None
    parts = line.split("\t")
    url = parts[0].strip()
    if not url:
        return None
    title = clean_title(parts[1]) if len(parts) > 1 else ""
    lookup = parts[2].strip() if len(parts) > 2 else ""
    if lookup != "failed":
        lookup = ""
    return url, title, lookup


def clean_title(value: str) -> str:
    return " ".join(value.replace("\t", " ").split())[:300]


def _format_line(url: str, title: str, lookup: str) -> str:
    if lookup == "failed":
        return f"{url}\t\tfailed"
    if title:
        return f"{url}\t{title}"
    return url


def read_entries(path: Path) -> list[tuple[str, str, str]]:
    """Read a complete queue file. A reader does not take the writer lock."""
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = []
    for raw in text.splitlines():
        parsed = parse_queue_line(raw)
        if parsed:
            entries.append(parsed)
    return entries


def read_queue(path: Path) -> list[str]:
    return [url for url, _title, _lookup in read_entries(path)]


def _queue_key(url: str) -> str:
    return youtube_url(url) or url.strip()


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _try_lock(path: Path, *, wait_seconds: float):
    """Exclusive lock on a sidecar file. Never locks queue.txt itself.

    Returns the open lock handle, or None when the lock is still busy after
    ``wait_seconds``. The caller must unlock and close it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = _lock_path(path).open("a+")
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(0.02)


def _unlock(handle) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _replace_lines(path: Path, lines: list[str]) -> None:
    """Replace the queue by renaming a finished temp file over it."""
    temporary = path.with_name(path.name + ".tmp")
    text = ("\n".join(lines) + "\n") if lines else ""
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def remove_queue(path: Path, url: str) -> str:
    """Remove one video from the queue.

    Returns ``removed``, ``absent`` when it was not in the file, or ``busy``
    when another writer holds the sidecar lock. A busy result waits for the
    next poll instead of blocking the bridge.
    """
    stored = _queue_key(url)
    if not stored or not path.is_file():
        return "absent"
    handle = _try_lock(path, wait_seconds=0)
    if handle is None:
        return "busy"
    try:
        kept: list[str] = []
        removed = False
        for entry_url, entry_title, entry_lookup in read_entries(path):
            if _queue_key(entry_url) == stored:
                removed = True
                continue
            kept.append(_format_line(entry_url, entry_title, entry_lookup))
        if not removed:
            return "absent"
        _replace_lines(path, kept)
        return "removed"
    finally:
        _unlock(handle)


def append_queue(path: Path, url: str) -> bool:
    """Append the URL. Return False when that video is already in the file."""
    stored = _queue_key(url)
    if not stored:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = _try_lock(path, wait_seconds=1)
    if handle is None:
        raise TimeoutError(f"Queue file is busy: {path}")
    try:
        existing = {_queue_key(url) for url, _title, _lookup in read_entries(path)}
        if stored in existing:
            return False
        lines = [_format_line(*entry) for entry in read_entries(path)]
        lines.append(stored)
        _replace_lines(path, lines)
        return True
    finally:
        _unlock(handle)


def set_title(path: Path, url: str, title: str) -> bool:
    """Write the title onto the matching queue line. The URL stays as stored."""
    return record_lookup(path, url, title=title, failed=False) == "written"


def record_lookup(path: Path, url: str, *, title: str = "", failed: bool = False) -> str:
    """Record a finished lookup without holding the lock across any network wait.

    Returns ``written`` when the file changed, ``ready`` when it already showed
    this outcome, and ``busy`` when the lock was taken by someone else.
    """
    stored = _queue_key(url)
    cleaned = clean_title(title)
    lookup = "failed" if failed and not cleaned else ""
    if not stored or not path.is_file():
        return "ready"
    if not cleaned and lookup != "failed":
        return "ready"
    handle = _try_lock(path, wait_seconds=0)
    if handle is None:
        return "busy"
    try:
        entries = read_entries(path)
        changed = False
        updated: list[str] = []
        for entry_url, entry_title, entry_lookup in entries:
            if _queue_key(entry_url) != stored:
                updated.append(_format_line(entry_url, entry_title, entry_lookup))
                continue
            if entry_title and lookup == "failed":
                updated.append(_format_line(entry_url, entry_title, ""))
                continue
            if entry_title == cleaned and entry_lookup == lookup:
                updated.append(_format_line(entry_url, entry_title, entry_lookup))
                continue
            updated.append(_format_line(entry_url, cleaned, lookup))
            changed = True
        if not changed:
            return "ready"
        _replace_lines(path, updated)
        return "written"
    finally:
        _unlock(handle)
