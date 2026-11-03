"""Local queue files. The inbox is how another run.sh call adds work."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from portal.config import MAX_PAYLOAD_CHARS, Settings
from portal.models import InboxRequest, Job, JobStatus, QueueState, utc_now


@contextmanager
def store_lock(settings: Settings) -> Iterator[None]:
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.assets_dir / "store.lock"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_state(settings: Settings) -> QueueState:
    path = settings.queue_state_file
    if not path.is_file():
        return QueueState()
    return QueueState.model_validate_json(path.read_text(encoding="utf-8"))


def save_state(settings: Settings, state: QueueState) -> None:
    path = settings.queue_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def enqueue(settings: Settings, kind: str, payload: str) -> str:
    kind = kind.strip()
    if not kind:
        raise ValueError("A job kind is required.")
    if len(payload) > MAX_PAYLOAD_CHARS:
        raise ValueError(f"Payload is longer than {MAX_PAYLOAD_CHARS} characters.")
    request = InboxRequest(kind=kind, payload=payload)
    job_id = _new_id()
    with store_lock(settings):
        settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        destination = settings.inbox_dir / f"{job_id}.json"
        temporary = settings.inbox_dir / f".{job_id}.json.tmp"
        temporary.write_text(request.model_dump_json(), encoding="utf-8")
        temporary.replace(destination)
    return job_id


def claim_next(settings: Settings) -> Job | None:
    with store_lock(settings):
        settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        waiting = sorted(
            path for path in settings.inbox_dir.glob("*.json") if path.is_file()
        )
        state = load_state(settings)
        known = {job.id for job in state.jobs}
        for ticket in waiting:
            if ticket.stem in known:
                ticket.unlink()
                continue
            request = InboxRequest.model_validate_json(ticket.read_text(encoding="utf-8"))
            now = utc_now()
            job = Job(
                id=ticket.stem,
                kind=request.kind,
                status=JobStatus.queued,
                payload=request.payload,
                detail="Waiting to run.",
                created_at=now,
                updated_at=now,
            )
            state.jobs.append(job)
            save_state(settings, state)
            ticket.unlink()
            return job
        return None


def update_job(
    settings: Settings,
    job_id: str,
    *,
    status: JobStatus,
    detail: str,
) -> Job:
    with store_lock(settings):
        state = load_state(settings)
        found: Job | None = None
        for index, job in enumerate(state.jobs):
            if job.id != job_id:
                continue
            found = job.model_copy(
                update={"status": status, "detail": detail, "updated_at": utc_now()}
            )
            state.jobs[index] = found
            break
        if found is None:
            raise KeyError(job_id)
        save_state(settings, state)
        return found


def list_visible(settings: Settings) -> list[Job]:
    """Saved jobs plus inbox tickets the bridge has not claimed yet."""
    with store_lock(settings):
        state = load_state(settings)
        jobs = list(state.jobs)
        if settings.inbox_dir.is_dir():
            for path in sorted(settings.inbox_dir.glob("*.json")):
                request = InboxRequest.model_validate_json(path.read_text(encoding="utf-8"))
                created = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                jobs.append(
                    Job(
                        id=path.stem,
                        kind=request.kind,
                        status=JobStatus.queued,
                        payload=request.payload,
                        detail="Held locally until the bridge claims it.",
                        created_at=created,
                        updated_at=created,
                    )
                )
        return jobs


def acquire_bridge_lock(settings: Settings):
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    handle = (settings.assets_dir / "bridge.lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit(
            "The bridge is already running in another terminal. "
            "Stop that one before starting a second."
        )
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _new_id() -> str:
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid4().hex[:8]}"
