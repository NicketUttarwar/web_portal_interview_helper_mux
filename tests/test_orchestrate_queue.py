from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from portal.orchestrate_queue import QueueOrchestrator
from portal.queue_file import append_queue, remove_queue
from tests.test_bridge import settings

WATCH = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
OTHER = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

_STUB = """#!/bin/sh
echo stub-stdout
echo stub-stderr >&2
{
  echo "ARGV:$*"
  echo "PYTHONPATH=${PYTHONPATH-}"
  echo "AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID-}"
  echo "MUX_FRESH=${MUX_FRESH-}"
  echo "PWD:$(pwd)"
} >> "$STUB_REPORT"
if [ -n "${STUB_SLEEP-}" ]; then
  sleep "$STUB_SLEEP"
fi
exit "${STUB_EXIT:-0}"
"""


def _cfg(tmp_path, *, gap: float = 0):
    cfg = settings(tmp_path)
    return replace(
        cfg,
        orchestrations_dir=tmp_path / "orchestrations",
        orchestrate_gap_seconds=gap,
    )


def _install_stub(cfg) -> Path:
    python = cfg.product_dir / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(_STUB, encoding="utf-8")
    python.chmod(0o755)
    return python


def _report(tmp_path) -> Path:
    return tmp_path / "stub-report.txt"


def _runs(cfg) -> list[Path]:
    root = cfg.orchestrations_dir
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _result(cfg) -> dict:
    runs = _runs(cfg)
    assert len(runs) == 1
    return json.loads((runs[0] / "result.json").read_text(encoding="utf-8"))


def _wait_until(predicate, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _finish(orch: QueueOrchestrator, cfg, url: str) -> None:
    assert _wait_until(
        lambda: orch.poll() and url not in cfg.queue_text_file.read_text(encoding="utf-8")
    )


def test_remove_queue_drops_one_video_and_keeps_the_rest(tmp_path):
    path = tmp_path / "queue.txt"
    assert append_queue(path, WATCH)
    assert append_queue(path, OTHER)
    from portal.queue_file import set_title

    assert set_title(path, OTHER, "Me at the zoo")
    assert remove_queue(path, "https://youtu.be/dQw4w9WgXcQ") == "removed"
    assert path.read_text(encoding="utf-8") == OTHER + "\tMe at the zoo\n"
    assert remove_queue(path, WATCH) == "absent"


def test_success_removes_the_url_and_records_the_command(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _install_stub(cfg)
    monkeypatch.setenv("STUB_REPORT", str(_report(tmp_path)))
    monkeypatch.setenv("PYTHONPATH", "/portal/src")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-PORTAL")
    monkeypatch.setenv("MUX_FRESH", "1")
    cfg.queue_text_file.write_text(WATCH + "\tRick\n", encoding="utf-8")
    orch = QueueOrchestrator(cfg)
    try:
        _finish(orch, cfg, WATCH)
    finally:
        orch.stop()
    assert cfg.queue_text_file.read_text(encoding="utf-8") == ""
    run = _runs(cfg)[0]
    request = json.loads((run / "request.json").read_text(encoding="utf-8"))
    assert request["argv"] == [
        str(cfg.product_dir / ".venv" / "bin" / "python"),
        "-m",
        "interview_mux",
        "orchestrate",
        "--mode",
        "full-auto",
        "--url",
        WATCH,
    ]
    assert request["cwd"] == str(cfg.product_dir)
    assert (run / "stdout.log").read_text(encoding="utf-8").strip() == "stub-stdout"
    assert (run / "stderr.log").read_text(encoding="utf-8").strip() == "stub-stderr"
    result = _result(cfg)
    assert result["outcome"] == "success"
    assert result["returncode"] == 0
    assert result["reason"] == "completed"
    assert result["removed_from_queue"] is True
    report = _report(tmp_path).read_text(encoding="utf-8")
    assert "PYTHONPATH=\n" in report
    assert "AWS_ACCESS_KEY_ID=\n" in report
    assert "MUX_FRESH=\n" in report
    assert f"PWD:{cfg.product_dir}" in report
    assert not (cfg.orchestrations_dir / "current.json").exists()


def test_a_failed_child_is_removed_once(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _install_stub(cfg)
    monkeypatch.setenv("STUB_REPORT", str(_report(tmp_path)))
    monkeypatch.setenv("STUB_EXIT", "1")
    cfg.queue_text_file.write_text(WATCH + "\n", encoding="utf-8")
    orch = QueueOrchestrator(cfg)
    try:
        _finish(orch, cfg, WATCH)
        for _ in range(5):
            assert orch.poll() is False
    finally:
        orch.stop()
    assert cfg.queue_text_file.read_text(encoding="utf-8") == ""
    result = _result(cfg)
    assert result["outcome"] == "failed"
    assert result["returncode"] == 1
    assert result["reason"] == "incomplete run or download failure"
    assert result["removed_from_queue"] is True
    assert _report(tmp_path).read_text(encoding="utf-8").count("ARGV:") == 1


def test_a_killed_child_is_recorded_and_removed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _install_stub(cfg)
    monkeypatch.setenv("STUB_REPORT", str(_report(tmp_path)))
    monkeypatch.setenv("STUB_SLEEP", "30")
    cfg.queue_text_file.write_text(WATCH + "\n", encoding="utf-8")
    orch = QueueOrchestrator(cfg)
    try:
        assert orch.poll() is False
        assert _wait_until(lambda: orch._active is not None and (cfg.orchestrations_dir / "current.json").is_file())
        current = json.loads((cfg.orchestrations_dir / "current.json").read_text(encoding="utf-8"))
        os.killpg(int(current["pid"]), signal.SIGKILL)
        _finish(orch, cfg, WATCH)
    finally:
        orch.stop()
    assert cfg.queue_text_file.read_text(encoding="utf-8") == ""
    result = _result(cfg)
    assert result["outcome"] == "failed"
    assert result["returncode"] < 0
    assert result["signal"]
    assert result["reason"] == f"killed by signal {result['signal']}"
    assert result["removed_from_queue"] is True


def test_the_next_url_waits_through_the_gap(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, gap=5)
    _install_stub(cfg)
    monkeypatch.setenv("STUB_REPORT", str(_report(tmp_path)))
    cfg.queue_text_file.write_text(WATCH + "\n" + OTHER + "\n", encoding="utf-8")
    clock = {"now": 0.0}
    orch = QueueOrchestrator(cfg, clock=lambda: clock["now"])
    try:
        assert orch.poll() is False
        assert _wait_until(lambda: orch._active is not None and orch._active.process.poll() is not None)
        assert orch.poll() is True
        assert cfg.queue_text_file.read_text(encoding="utf-8").splitlines() == [OTHER]
        assert orch.poll() is False
        assert _report(tmp_path).read_text(encoding="utf-8").count("ARGV:") == 1
        clock["now"] = 5
        assert orch.poll() is False
        assert _wait_until(lambda: _report(tmp_path).read_text(encoding="utf-8").count("ARGV:") == 2)
    finally:
        orch.stop()


def test_a_missing_product_python_leaves_the_queue(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    cfg.product_dir.mkdir(parents=True)
    cfg.queue_text_file.write_text(WATCH + "\n", encoding="utf-8")
    orch = QueueOrchestrator(cfg)
    assert orch.poll() is False
    assert orch.poll() is False
    assert cfg.queue_text_file.read_text(encoding="utf-8") == WATCH + "\n"
    assert not cfg.orchestrations_dir.exists() or _runs(cfg) == []
    text = capsys.readouterr().out
    assert text.count("Product python is missing") == 1


def test_stopping_the_portal_leaves_the_url(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _install_stub(cfg)
    monkeypatch.setenv("STUB_REPORT", str(_report(tmp_path)))
    monkeypatch.setenv("STUB_SLEEP", "30")
    cfg.queue_text_file.write_text(WATCH + "\tRick\n" + OTHER + "\n", encoding="utf-8")
    orch = QueueOrchestrator(cfg)
    assert orch.poll() is False
    assert _wait_until(lambda: orch._active is not None)
    pid = orch._active.pid
    orch.stop()
    assert cfg.queue_text_file.read_text(encoding="utf-8") == WATCH + "\tRick\n" + OTHER + "\n"
    result = _result(cfg)
    assert result["outcome"] == "interrupted"
    assert result["reason"] == "portal stopped"
    assert result["removed_from_queue"] is False
    assert not (cfg.orchestrations_dir / "current.json").exists()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_living_pid_is_adopted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _install_stub(cfg)
    report = _report(tmp_path)
    monkeypatch.setenv("STUB_REPORT", str(report))
    cfg.queue_text_file.write_text(WATCH + "\n" + OTHER + "\n", encoding="utf-8")
    run_dir = cfg.orchestrations_dir / "adopted"
    run_dir.mkdir(parents=True)
    exit_file = run_dir / "exit_code.txt"
    sleeper = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 30"],
        start_new_session=True,
    )
    current = {
        "pid": sleeper.pid,
        "url": WATCH,
        "run_dir": str(run_dir),
        "exit_file": str(exit_file),
        "started_at": "2026-11-03T00:00:00+00:00",
    }
    cfg.orchestrations_dir.mkdir(parents=True, exist_ok=True)
    (cfg.orchestrations_dir / "current.json").write_text(json.dumps(current), encoding="utf-8")
    orch = QueueOrchestrator(cfg)
    try:
        assert orch.poll() is False
        assert cfg.queue_text_file.read_text(encoding="utf-8").startswith(WATCH)
        assert not report.exists()
        exit_file.write_text("0\n", encoding="utf-8")
        os.kill(sleeper.pid, signal.SIGKILL)
        sleeper.wait(timeout=5)
        assert orch.poll() is True
    finally:
        orch.stop()
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)
    assert cfg.queue_text_file.read_text(encoding="utf-8") == OTHER + "\n"
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["outcome"] == "success"
    assert result["returncode"] == 0
    assert result["removed_from_queue"] is True
    assert not report.exists()


def test_a_line_that_is_not_youtube_is_removed_without_the_product(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _install_stub(cfg)
    monkeypatch.setenv("STUB_REPORT", str(_report(tmp_path)))
    cfg.queue_text_file.write_text("not-a-video\n" + WATCH + "\n", encoding="utf-8")
    orch = QueueOrchestrator(cfg)
    try:
        assert orch.poll() is True
        assert cfg.queue_text_file.read_text(encoding="utf-8") == WATCH + "\n"
        assert not _report(tmp_path).exists()
        failed = _result(cfg)
        assert failed["outcome"] == "failed"
        assert failed["reason"] == "not a YouTube URL"
        assert failed["removed_from_queue"] is True
    finally:
        orch.stop()
