from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.background_jobs.constants import (
    BACKGROUND_JOBS_DEFAULT_QUEUE,
    BACKGROUND_JOBS_RUN_JOB_NAME,
)
from app.core.background_jobs.repository import PostgresJobRepository
from app.core.background_jobs.types import (
    EnqueueJobInput,
    EnqueueJobResult,
    InsertPendingJobInput,
)

logger = logging.getLogger("JobEnqueueService")


class JobEnqueueService:
    def __init__(
        self,
        repository: PostgresJobRepository,
        queue_dispatcher: Any = None,
        default_queue_name: str = BACKGROUND_JOBS_DEFAULT_QUEUE,
    ) -> None:
        self._repo = repository
        self._dispatcher = queue_dispatcher
        self._default_queue_name = default_queue_name

    async def enqueue(self, input_data: EnqueueJobInput) -> EnqueueJobResult:
        queue_name = input_data.queue_name or self._default_queue_name
        run_at = input_data.run_at or datetime.now(UTC)
        max_attempts = (
            input_data.max_attempts if input_data.max_attempts is not None else 5
        )

        job_id = await self._repo.insert_pending(
            InsertPendingJobInput(
                queue_name=queue_name,
                job_name=input_data.job_name,
                dedupe_key=input_data.dedupe_key,
                payload=input_data.payload,
                run_at=run_at,
                max_attempts=max_attempts,
            )
        )

        if not job_id:
            return EnqueueJobResult(job_row_id="", enqueued=False, deduplicated=True)

        if self._dispatcher is not None:
            try:
                dispatch_fn = getattr(self._dispatcher, "dispatch", None) or getattr(
                    self._dispatcher, "add", None
                )
                if callable(dispatch_fn):
                    res = dispatch_fn(
                        BACKGROUND_JOBS_RUN_JOB_NAME,
                        {"jobRowId": job_id},
                    )
                    if hasattr(res, "__await__"):
                        await res
            except Exception as exc:
                logger.warning(
                    "Failed to dispatch wake event for job %s: %s", job_id, exc
                )

        return EnqueueJobResult(job_row_id=job_id, enqueued=True, deduplicated=False)

    async def wake_job_row(self, job_row_id: str, max_attempts: int = 5) -> None:
        if self._dispatcher is None:
            return

        get_job_fn = getattr(self._dispatcher, "get_job", None) or getattr(
            self._dispatcher, "getJob", None
        )
        if callable(get_job_fn):
            existing = get_job_fn(job_row_id)
            if hasattr(existing, "__await__"):
                existing = await existing
            if existing is not None:
                get_state_fn = getattr(existing, "get_state", None) or getattr(
                    existing, "getState", None
                )
                if callable(get_state_fn):
                    state = get_state_fn()
                    if hasattr(state, "__await__"):
                        state = await state
                    if state in ("active", "waiting", "delayed", "prioritized"):
                        return
                remove_fn = getattr(existing, "remove", None)
                if callable(remove_fn):
                    res_rem = remove_fn()
                    if hasattr(res_rem, "__await__"):
                        await res_rem

        dispatch_fn = getattr(self._dispatcher, "dispatch", None) or getattr(
            self._dispatcher, "add", None
        )
        if callable(dispatch_fn):
            res = dispatch_fn(
                BACKGROUND_JOBS_RUN_JOB_NAME,
                {"jobRowId": job_row_id},
            )
            if hasattr(res, "__await__"):
                await res
