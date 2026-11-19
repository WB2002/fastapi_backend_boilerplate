from __future__ import annotations

from typing import Final

BACKGROUND_JOBS_DEFAULT_QUEUE: Final[str] = "default"
BACKGROUND_JOBS_RUN_JOB_NAME: Final[str] = "background_jobs:run"
BACKGROUND_JOBS_LEASE_EXPIRED_MESSAGE: Final[str] = "lease expired"
DEFAULT_LEASE_DURATION_SEC: Final[float] = 300.0
