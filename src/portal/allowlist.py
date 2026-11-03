"""Fixed commands the bridge is willing to start."""

from __future__ import annotations

import os
import signal
import subprocess
import sys

from portal.config import Settings

# kind -> module run as its own process. The payload is never a shell command.
HANDLERS: dict[str, str] = {
    "echo": "portal.handlers.echo",
}


def is_allowed(kind: str) -> bool:
    return kind in HANDLERS


def job_env(settings: Settings) -> dict[str, str]:
    """A small environment. AWS credentials from the parent are not copied."""
    kept = ("PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT")
    env = {key: os.environ[key] for key in kept if os.environ.get(key)}
    env["PYTHONPATH"] = str(settings.root / "src")
    env["PYTHONNOUSERSITE"] = "1"
    return env


def run_handler(settings: Settings, module: str, job_dir: str) -> tuple[int, str]:
    process = subprocess.Popen(
        [sys.executable, "-m", module, job_dir],
        cwd=job_dir,
        env=job_env(settings),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _stdout, stderr = process.communicate(timeout=settings.job_timeout_seconds)
    except subprocess.TimeoutExpired:
        _stop_group(process)
        return 124, "The job exceeded its time limit and was stopped."
    detail = (stderr or "").strip()
    if len(detail) > 500:
        detail = detail[:500]
    return process.returncode or 0, detail


def _stop_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.communicate(timeout=5)
