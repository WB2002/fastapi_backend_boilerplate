from __future__ import annotations

import logging
import os
import platform
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.background_jobs.constants import DEFAULT_LEASE_DURATION_SEC
from app.core.background_jobs.handlers import JobHandlerRegistry
from app.core.background_jobs.repository import PostgresJobRepository
from app.core.background_jobs.types import BackgroundJobRow, ClaimJobInput

logger = logging.getLogger("BackgroundJobsRunProcessor")


class BackgroundJobsRunProcessor:
    def __init__(
        self,
        repository: PostgresJobRepository,
        registry: JobHandlerRegistry,
        claim_lease_sec: float = DEFAULT_LEASE_DURATION_SEC,
    ) -> None:
        self._repo = repository
        self._registry = registry
        self._claim_lease_sec = claim_lease_sec

    async def process(self, job: Any) -> None:
        job_row_id: str | None = None
        if isinstance(job, dict):
            raw_data = job.get("data")
            sub_id = raw_data.get("jobRowId") if isinstance(raw_data, dict) else None
            job_row_id = job.get("jobRowId") or sub_id or job.get("id")
        else:
            raw_data = getattr(job, "data", None)
            if isinstance(raw_data, dict):
                job_row_id = raw_data.get("jobRowId")
            else:
                job_row_id = getattr(job, "job_row_id", None) or getattr(
                    job, "id", None
                )

        if not job_row_id or not isinstance(job_row_id, str):
            raise ValueError(f"Invalid job payload without jobRowId: {job}")

        worker_id = f"{platform.node()}:{os.getpid()}"
        row = await self._repo.claim_by_id(
            ClaimJobInput(
                id=job_row_id,
                worker_id=worker_id,
                lease_sec=self._claim_lease_sec,
            )
        )

        if row is None:
            status = await self._repo.get_status(job_row_id)
            if status in ("completed", "cancelled"):
                return
            if status == "processing":
                logger.debug("Skip delivery; job %s already processing", job_row_id)
                return
            err_msg = (
                f"Unclaimable background job {job_row_id} "
                f"(status={status or 'missing'})"
            )
            raise RuntimeError(err_msg)

        await self._execute_job(row)

    async def _execute_job(self, row: BackgroundJobRow) -> None:
        try:
            handler = self._registry.get(row.job_name)
            await handler.handle(row.payload)
            await self._repo.mark_completed(row.id)
        except Exception as err:
            logger.warning(
                "Job execution failed for job %s (%s): %s",
                row.id,
                row.job_name,
                err,
            )
            if row.attempts >= row.max_attempts:
                await self._repo.mark_failed(row.id, err)
                return
            delay_sec = float(row.attempts)
            requeue_at = datetime.now(UTC) + timedelta(seconds=delay_sec)
            await self._repo.mark_failed(row.id, err, requeue_at)
