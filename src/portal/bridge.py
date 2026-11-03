"""The long-running process. It owns the local queue and refreshes the site."""

from __future__ import annotations

import signal
import time
from uuid import uuid4

from portal.config import Settings, load_settings
from portal.jobs import run_one
from portal.publish import QueuePublisher
from portal.store import acquire_bridge_lock, list_visible, load_state


def serve(settings: Settings | None = None) -> int:
    settings = settings or load_settings()
    lock = acquire_bridge_lock(settings)
    stop = False

    def request_stop(_signum: int, _frame) -> None:
        nonlocal stop
        stop = True
        print("Stopping after the current step.", flush=True)

    for caught in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(caught, request_stop)

    publisher = QueuePublisher(settings)
    run_id = uuid4().hex
    print(
        "Bridge running. The website light stays on while this process is alive. "
        f"Product checkout: {settings.product_dir}",
        flush=True,
    )
    publisher.discard_uploads()
    publisher.publish_presence(active=True, run_id=run_id)
    _publish(publisher, settings)
    last_publish = time.monotonic()
    last_status = time.monotonic()
    try:
        while not stop:
            changed = run_one(settings) is not None
            if publisher.accept_uploads(run_id):
                changed = True
            now = time.monotonic()
            if changed or now - last_publish >= settings.publish_interval_seconds:
                _publish(publisher, settings)
                last_publish = now
            if now - last_status >= settings.status_interval_seconds:
                publisher.publish_presence(active=True, run_id=run_id)
                last_status = now
            if stop:
                break
            time.sleep(0.4)
    finally:
        publisher.publish_presence(active=False, run_id=run_id)
        publisher.discard_uploads(run_id)
        _publish(publisher, settings)
        lock.close()
    print("Bridge stopped. The website light is off and new text is not accepted.", flush=True)
    return 0


def _publish(publisher: QueuePublisher, settings: Settings) -> None:
    publisher.publish(load_state(settings).jobs)


def format_status(settings: Settings) -> str:
    jobs = list_visible(settings)
    if not jobs:
        return "Queue is empty."
    lines = ["id\tkind\tstatus\tdetail"]
    for job in sorted(jobs, key=lambda item: item.created_at, reverse=True):
        lines.append(f"{job.id}\t{job.kind}\t{job.status.value}\t{job.detail}")
    return "\n".join(lines)
