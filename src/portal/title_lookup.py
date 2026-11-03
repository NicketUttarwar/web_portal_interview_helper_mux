"""Look up a YouTube title in a child process, then record the outcome.

The child only talks to YouTube and prints the title. It does not open the
queue file. The bridge writes the title, or ``failed``, after the child exits
or after the lookup deadline. That write is a short rename under a sidecar
lock, so a crashed or killed lookup cannot hold the queue file.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from portal.allowlist import job_env
from portal.config import Settings
from portal.queue_file import clean_title, read_entries, record_lookup, youtube_url

_OEMBED = "https://www.youtube.com/oembed"
_MAX_LOOKUP_SECONDS = 30.0


def fetch_title(url: str, *, timeout: float = _MAX_LOOKUP_SECONDS) -> str:
    """The video title as plain text. Empty when the URL is not a YouTube video."""
    canonical = youtube_url(url)
    if not canonical:
        return ""
    limit = min(_MAX_LOOKUP_SECONDS, max(1.0, timeout))
    query = urllib.parse.urlencode({"url": canonical, "format": "json"})
    request = urllib.request.Request(
        f"{_OEMBED}?{query}",
        headers={"User-Agent": "ZeroShotQueue/1.0"},
    )
    with urllib.request.urlopen(request, timeout=limit) as response:
        payload = json.loads(response.read().decode("utf-8"))
    title = payload.get("title") if isinstance(payload, dict) else ""
    return clean_title(title) if isinstance(title, str) else ""


@dataclass
class _Running:
    process: subprocess.Popen[str]
    started: float


class TitleLookups:
    """One child per video, started only while the bridge is running."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = min(
            _MAX_LOOKUP_SECONDS,
            max(1.0, float(settings.title_lookup_timeout_seconds)),
        )
        self._kids: dict[str, _Running] = {}
        self._started: set[str] = set()
        self._pending: dict[str, str] = {}

    def spawn_missing(self) -> None:
        for url, title, lookup in read_entries(self.settings.queue_text_file):
            canonical = youtube_url(url)
            if not canonical or title or lookup == "failed" or canonical in self._started:
                continue
            self._started.add(canonical)
            print(f"Looking up title: {canonical}", flush=True)
            self._kids[canonical] = _Running(
                process=subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "portal.title_lookup",
                        canonical,
                        str(self.timeout),
                    ],
                    cwd=str(self.settings.root),
                    env=job_env(self.settings),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    start_new_session=True,
                ),
                started=time.monotonic(),
            )

    def poll(self) -> bool:
        """Finish lookups that exited or passed the deadline. Never waits on them."""
        now = time.monotonic()
        changed = self._flush_results()
        for url, running in list(self._kids.items()):
            code = running.process.poll()
            expired = now - running.started >= self.timeout
            if code is None and not expired:
                continue
            if code is None:
                _end_process(running.process)
                print(f"Title lookup timed out for {url}.", flush=True)
                self._pending[url] = ""
            else:
                title = _stdout_title(running.process) if code == 0 else ""
                self._pending[url] = title
                if not title:
                    print(f"Title lookup unsuccessful for {url}.", flush=True)
            self._kids.pop(url)
            changed = True
        if self._flush_results():
            changed = True
        return changed

    def stop(self) -> None:
        """End children that are still running. An interrupt is not a failed lookup."""
        for running in self._kids.values():
            _end_process(running.process)
        self._kids.clear()
        self._flush_results()

    def _flush_results(self) -> bool:
        wrote = False
        for url, title in list(self._pending.items()):
            outcome = record_lookup(
                self.settings.queue_text_file,
                url,
                title=title,
                failed=not title,
            )
            if outcome == "busy":
                continue
            self._pending.pop(url)
            wrote = True
            if title:
                print(f"Title: {title}", flush=True)
            else:
                print(f"Recorded unsuccessful title lookup for {url}.", flush=True)
        return wrote


def _stdout_title(process: subprocess.Popen[str]) -> str:
    stdout = process.stdout
    if stdout is None:
        return ""
    try:
        raw = stdout.read()
    except OSError:
        return ""
    line = raw.splitlines()[0] if raw else ""
    return clean_title(line)


def _end_process(process: subprocess.Popen[str]) -> None:
    """Stop a lookup child without waiting on its output or the queue file."""
    if process.poll() is not None:
        return
    pid = getattr(process, "pid", None)
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        return


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        print("usage: python -m portal.title_lookup URL [TIMEOUT_SECONDS]", file=sys.stderr)
        return 2
    url = args[0]
    try:
        timeout = float(args[1]) if len(args) == 2 else _MAX_LOOKUP_SECONDS
    except ValueError:
        print("timeout must be a number of seconds", file=sys.stderr)
        return 2
    try:
        title = fetch_title(url, timeout=timeout)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        print(f"Title lookup failed for {url}: {exc}", file=sys.stderr, flush=True)
        return 1
    if not title:
        print(f"No title for {url}", file=sys.stderr, flush=True)
        return 1
    print(title, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
