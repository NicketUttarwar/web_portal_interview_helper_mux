"""Status rows stored on this machine and published to the site."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    finished = "finished"
    failed = "failed"
    rejected = "rejected"


class Job(BaseModel):
    id: str
    kind: str
    status: JobStatus
    payload: str = ""
    detail: str = ""
    created_at: datetime
    updated_at: datetime


class QueueState(BaseModel):
    jobs: list[Job] = Field(default_factory=list)


class InboxRequest(BaseModel):
    kind: str
    payload: str = ""
