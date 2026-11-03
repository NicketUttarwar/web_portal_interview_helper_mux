"""Claim one inbox ticket and run it, or reject it when the kind is unknown."""

from __future__ import annotations

import json

from portal.allowlist import HANDLERS, is_allowed, run_handler
from portal.config import Settings
from portal.models import Job, JobStatus
from portal.store import claim_next, update_job


def run_one(settings: Settings) -> Job | None:
    job = claim_next(settings)
    if job is None:
        return None
    if not is_allowed(job.kind):
        return update_job(
            settings,
            job.id,
            status=JobStatus.rejected,
            detail="This kind is not allowlisted, so it was not started.",
        )
    job_dir = settings.job_dir(job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    request = {"kind": job.kind, "payload": job.payload}
    (job_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
    update_job(settings, job.id, status=JobStatus.running, detail="Running.")
    code, detail = run_handler(settings, HANDLERS[job.kind], str(job_dir))
    if code == 0:
        return update_job(
            settings,
            job.id,
            status=JobStatus.finished,
            detail="Finished. The payload is in the job folder.",
        )
    return update_job(
        settings,
        job.id,
        status=JobStatus.failed,
        detail=detail or f"The handler exited with status {code}.",
    )
