"""Drain queue.txt by running one full-auto episode at a time.

The child is interview_helper_mux's blocking orchestrate command. This module
polls that child and never waits inside the bridge loop. Exit 0 removes the
URL. Any other exit while the bridge stays up is one recorded failure, then
the URL is removed. Stopping the bridge kills the child and leaves the URL.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from portal.config import Settings
from portal.queue_file import read_entries, remove_queue, youtube_url

_EXIT_GRACE_SECONDS = 2.0
_AWS_ENV = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_PROFILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
}

# The product command stays the orchestrate invocation. This parent records
# the product's exit code and then exits with it, so a later portal process
# can adopt the pid and still read the code after a hard kill.
_FORWARD = """
import subprocess
import sys
from pathlib import Path

exit_file = Path(sys.argv[1])
completed = subprocess.run(sys.argv[2:])
code = completed.returncode
exit_file.write_text(str(code) + "\\n", encoding="utf-8")
raise SystemExit(code if code >= 0 else 128 + (-code))
"""


class _Running:
    pass


RUNNING = _Running()


@dataclass
class _Active:
    url: str
    run_dir: Path
    exit_file: Path
    started_at: str
    process: subprocess.Popen[bytes] | None
    pid: int


@dataclass
class _Settle:
    url: str
    run_dir: Path
    code: int | None
    reason: str


def child_env() -> dict[str, str]:
    """The product environment. Portal import path and cloud credentials stay here."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _AWS_ENV and not key.startswith("MUX_")
    }
    env.pop("PYTHONPATH", None)
    return env


class QueueOrchestrator:
    """One child, the head of queue.txt, then a gap before the next head."""

    def __init__(self, settings: Settings, clock=time.monotonic) -> None:
        self.settings = settings
        self._clock = clock
        self._active: _Active | None = None
        self._settle: _Settle | None = None
        self._ready_at: float | None = None
        self._lost_since: float | None = None
        self._missing_logged = False
        self._restore()

    def poll(self) -> bool:
        """Advance one step. True when a URL left the queue."""
        if self._settle is not None:
            return self._finish_settle()
        if self._active is not None:
            observed = self._observe()
            if observed is RUNNING:
                return False
            return self._begin_settle(observed)
        if self._ready_at is not None and self._clock() < self._ready_at:
            return False
        self._ready_at = None
        return self._start_head()

    def stop(self) -> None:
        """End the child because the portal is stopping. The URL stays queued."""
        if self._settle is not None:
            self._finish_settle()
            return
        active = self._active
        if active is None:
            return
        observed = self._observe()
        if observed is not RUNNING:
            self._begin_settle(observed)
            return
        code = self._kill(active)
        self._write_result(
            active.run_dir,
            url=active.url,
            code=code,
            reason="portal stopped",
            outcome="interrupted",
            removed=False,
        )
        self._clear_current()
        self._active = None
        print(f"Full-auto interrupted: {active.url}", flush=True)

    def _start_head(self) -> bool:
        entries = read_entries(self.settings.queue_text_file)
        if not entries:
            return False
        url = entries[0][0]
        canonical = youtube_url(url)
        if canonical is None:
            run_dir = self._make_run_dir(url)
            self._write_request(run_dir, url, argv=[], started_at=_now())
            _touch(run_dir / "stdout.log")
            _touch(run_dir / "stderr.log")
            self._settle = _Settle(url, run_dir, None, "not a YouTube URL")
            return self._finish_settle()
        python = self._python()
        if python is None:
            self._log_missing()
            return False
        return self._spawn(canonical, python)

    def _spawn(self, url: str, python: Path) -> bool:
        run_dir = self._make_run_dir(url)
        exit_file = run_dir / "exit_code.txt"
        argv = [
            str(python),
            "-m",
            "interview_mux",
            "orchestrate",
            "--mode",
            "full-auto",
            "--url",
            url,
        ]
        started_at = _now()
        self._write_request(run_dir, url, argv, started_at)
        stdout = (run_dir / "stdout.log").open("wb")
        stderr = (run_dir / "stderr.log").open("wb")
        try:
            process = subprocess.Popen(
                [sys.executable, "-c", _FORWARD, str(exit_file), *argv],
                cwd=self.settings.product_dir,
                env=child_env(),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError as exc:
            self._settle = _Settle(url, run_dir, None, f"failed to start: {exc}")
            return self._finish_settle()
        finally:
            stdout.close()
            stderr.close()
        active = _Active(url, run_dir, exit_file, started_at, process, process.pid)
        self._active = active
        self._lost_since = None
        self._write_current(active)
        print(f"Full-auto started: {url}", flush=True)
        return False

    def _begin_settle(self, code: int | None) -> bool:
        active = self._active
        if active is None:
            return False
        self._active = None
        self._settle = _Settle(active.url, active.run_dir, code, _reason(code))
        return self._finish_settle()

    def _finish_settle(self) -> bool:
        settle = self._settle
        if settle is None:
            return False
        outcome = remove_queue(self.settings.queue_text_file, settle.url)
        if outcome == "busy":
            return False
        self._write_result(
            settle.run_dir,
            url=settle.url,
            code=settle.code,
            reason=settle.reason,
            outcome="success" if settle.code == 0 else "failed",
            removed=outcome == "removed",
        )
        self._settle = None
        self._clear_current()
        self._ready_at = self._clock() + float(self.settings.orchestrate_gap_seconds)
        if settle.code == 0:
            print(f"Full-auto completed: {settle.url}", flush=True)
        else:
            print(f"Full-auto failed: {settle.url} ({settle.reason})", flush=True)
        return True

    def _observe(self) -> int | None | _Running:
        active = self._active
        if active is None:
            return RUNNING
        if active.process is not None:
            status = active.process.poll()
            if status is None:
                self._lost_since = None
                return RUNNING
            return self._code_after_exit(status)
        try:
            waited, raw = os.waitpid(active.pid, os.WNOHANG)
        except ChildProcessError:
            if _alive(active.pid):
                self._lost_since = None
                return RUNNING
            return self._code_after_exit(None)
        if waited == 0:
            self._lost_since = None
            return RUNNING
        return self._code_after_exit(_decode_wait(raw))

    def _code_after_exit(self, status: int | None) -> int | None | _Running:
        active = self._active
        if active is None:
            return status
        file_code = _read_exit_file(active.exit_file)
        if file_code is not None:
            return file_code
        if status is not None and status < 0:
            return status
        if self._grace_waiting():
            return RUNNING
        return status

    def _grace_waiting(self) -> bool:
        now = self._clock()
        if self._lost_since is None:
            self._lost_since = now
            return True
        return now - self._lost_since < _EXIT_GRACE_SECONDS

    def _restore(self) -> None:
        path = self._current_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            active = _Active(
                url=str(data["url"]),
                run_dir=Path(str(data["run_dir"])),
                exit_file=Path(str(data["exit_file"])),
                started_at=str(data.get("started_at") or ""),
                process=None,
                pid=int(data["pid"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            path.unlink(missing_ok=True)
            return
        self._active = active
        observed = self._observe()
        if observed is RUNNING:
            print(f"Adopted full-auto: {active.url}", flush=True)
            return
        self._begin_settle(observed)

    def _kill(self, active: _Active) -> int | None:
        pid = active.process.pid if active.process is not None else active.pid
        _signal_tree(pid, signal.SIGTERM)
        if active.process is not None:
            try:
                return active.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_tree(pid, signal.SIGKILL)
                return active.process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.05)
        if _alive(pid):
            _signal_tree(pid, signal.SIGKILL)
        try:
            waited, raw = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return None
        if waited == 0:
            return None
        return _decode_wait(raw)

    def _python(self) -> Path | None:
        path = self._python_path()
        if path.is_file() and os.access(path, os.X_OK):
            return path
        return None

    def _python_path(self) -> Path:
        return self.settings.product_dir / ".venv" / "bin" / "python"

    def _log_missing(self) -> None:
        if self._missing_logged:
            return
        self._missing_logged = True
        print(
            f"Product python is missing: {self._python_path()}. The queue was left unchanged.",
            flush=True,
        )

    def _make_run_dir(self, url: str) -> Path:
        canonical = youtube_url(url)
        ident = canonical.split("v=", 1)[1] if canonical and "v=" in canonical else "invalid"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self._dir() / f"{stamp}-{ident}-{uuid4().hex[:6]}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _dir(self) -> Path:
        path = self.settings.orchestrations_dir
        if not path.is_absolute():
            path = self.settings.root / path
        return path

    def _current_path(self) -> Path:
        return self._dir() / "current.json"

    def _write_current(self, active: _Active) -> None:
        payload = {
            "pid": active.pid,
            "url": active.url,
            "run_dir": str(active.run_dir),
            "exit_file": str(active.exit_file),
            "started_at": active.started_at,
        }
        path = self._current_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _clear_current(self) -> None:
        self._current_path().unlink(missing_ok=True)

    def _write_request(self, run_dir: Path, url: str, argv: list[str], started_at: str) -> None:
        payload = {
            "url": url,
            "argv": argv,
            "cwd": str(self.settings.product_dir),
            "started_at": started_at,
        }
        (run_dir / "request.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_result(
        self,
        run_dir: Path,
        *,
        url: str,
        code: int | None,
        reason: str,
        outcome: str,
        removed: bool,
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": url,
            "outcome": outcome,
            "returncode": code,
            "signal": _signal_name(code),
            "reason": reason,
            "ended_at": _now(),
            "removed_from_queue": removed,
        }
        (run_dir / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reason(code: int | None) -> str:
    if code is None:
        return "child ended without an exit code"
    if code == 0:
        return "completed"
    if code == 1:
        return "incomplete run or download failure"
    if code == 2:
        return "command rejected"
    if code < 0:
        return f"killed by signal {_signal_name(code)}"
    return f"exited with status {code}"


def _signal_name(code: int | None) -> str | None:
    if code is None or code >= 0:
        return None
    try:
        return signal.Signals(-code).name
    except ValueError:
        return f"SIG{-code}"


def _read_exit_file(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _decode_wait(status: int) -> int:
    if os.WIFEXITED(status):
        return int(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        return -int(os.WTERMSIG(status))
    return 1


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_tree(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
