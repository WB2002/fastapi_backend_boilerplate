from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

BackgroundJobStatus = Literal[
    "pending",
    "processing",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(slots=True)
class BackgroundJobRow:
    id: str
    queue_name: str
    job_name: str
    payload: Any
    attempts: int
    max_attempts: int

    @property
    def queueName(self) -> str:
        return self.queue_name

    @property
    def jobName(self) -> str:
        return self.job_name

    @property
    def maxAttempts(self) -> int:
        return self.max_attempts


@dataclass(slots=True)
class ClaimJobInput:
    id: str
    worker_id: str
    lease_sec: float

    @property
    def workerId(self) -> str:
        return self.worker_id

    @property
    def leaseSec(self) -> float:
        return self.lease_sec


@dataclass(slots=True)
class InsertPendingJobInput:
    queue_name: str
    job_name: str
    payload: Any
    run_at: datetime
    max_attempts: int
    dedupe_key: str | None = None

    @property
    def queueName(self) -> str:
        return self.queue_name

    @property
    def jobName(self) -> str:
        return self.job_name

    @property
    def runAt(self) -> datetime:
        return self.run_at

    @property
    def maxAttempts(self) -> int:
        return self.max_attempts

    @property
    def dedupeKey(self) -> str | None:
        return self.dedupe_key


@dataclass(slots=True)
class ReclaimedLeaseRow:
    id: str
    status: BackgroundJobStatus
    max_attempts: int

    @property
    def maxAttempts(self) -> int:
        return self.max_attempts


@dataclass(slots=True)
class EnqueueJobInput:
    job_name: str
    payload: Any
    queue_name: str | None = None
    run_at: datetime | None = None
    max_attempts: int | None = None
    dedupe_key: str | None = None

    @property
    def jobName(self) -> str:
        return self.job_name

    @property
    def queueName(self) -> str | None:
        return self.queue_name

    @property
    def runAt(self) -> datetime | None:
        return self.run_at

    @property
    def maxAttempts(self) -> int | None:
        return self.max_attempts

    @property
    def dedupeKey(self) -> str | None:
        return self.dedupe_key


@dataclass(slots=True)
class EnqueueJobResult:
    job_row_id: str
    enqueued: bool
    deduplicated: bool

    @property
    def jobRowId(self) -> str:
        return self.job_row_id
