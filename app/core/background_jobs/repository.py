from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.common.postgres.postgres_pool import PostgresPool
from app.core.background_jobs.constants import (
    BACKGROUND_JOBS_LEASE_EXPIRED_MESSAGE,
)
from app.core.background_jobs.types import (
    BackgroundJobRow,
    BackgroundJobStatus,
    ClaimJobInput,
    InsertPendingJobInput,
    ReclaimedLeaseRow,
)

logger = logging.getLogger("PostgresJobRepository")

CLAIM_JOB_BY_ID_SQL = """
WITH picked AS (
  SELECT id
  FROM background_jobs
  WHERE id = $1::uuid
    AND status = 'pending'::background_job_status
    AND run_at <= NOW()
  FOR UPDATE SKIP LOCKED
)
UPDATE background_jobs AS j
SET
  status = 'processing'::background_job_status,
  locked_by = $2,
  locked_at = NOW(),
  lease_until = NOW() + make_interval(secs => $3::double precision),
  attempts = j.attempts + 1,
  updated_at = NOW()
FROM picked
WHERE j.id = picked.id
RETURNING
  j.id,
  j.queue_name,
  j.job_name,
  j.payload,
  j.attempts,
  j.max_attempts;
"""

INSERT_PENDING_JOB_SQL = """
INSERT INTO background_jobs (
  queue_name,
  job_name,
  dedupe_key,
  payload,
  run_at,
  max_attempts
) VALUES ($1, $2, $3, $4::jsonb, $5, $6)
ON CONFLICT (dedupe_key)
  WHERE dedupe_key IS NOT NULL
    AND status IN (
      'pending'::background_job_status,
      'processing'::background_job_status
    )
  DO NOTHING
RETURNING id;
"""

MARK_JOB_COMPLETED_SQL = """
UPDATE background_jobs
SET
  status = 'completed'::background_job_status,
  updated_at = NOW(),
  completed_at = NOW(),
  lease_until = NULL,
  locked_by = NULL,
  locked_at = NULL
WHERE id = $1::uuid;
"""

MARK_JOB_FAILED_SQL = """
UPDATE background_jobs
SET
  status = 'failed'::background_job_status,
  updated_at = NOW(),
  last_error = $2,
  lease_until = NULL,
  locked_by = NULL,
  locked_at = NULL,
  run_at = COALESCE($3::timestamptz, run_at)
WHERE id = $1::uuid;
"""

REQUEUE_JOB_FOR_RETRY_SQL = """
UPDATE background_jobs
SET
  status = 'pending'::background_job_status,
  updated_at = NOW(),
  last_error = $2,
  lease_until = NULL,
  locked_by = NULL,
  locked_at = NULL,
  run_at = $3::timestamptz
WHERE id = $1::uuid
  AND attempts < max_attempts;
"""

GET_JOB_STATUS_SQL = """
SELECT status::text AS status
FROM background_jobs
WHERE id = $1::uuid;
"""

RECLAIM_EXPIRED_LEASES_SQL = """
WITH picked AS (
  SELECT id, attempts, max_attempts
  FROM background_jobs
  WHERE status = 'processing'::background_job_status
    AND lease_until IS NOT NULL
    AND lease_until < NOW()
  ORDER BY lease_until ASC
  LIMIT $1::int
  FOR UPDATE SKIP LOCKED
)
UPDATE background_jobs AS j
SET
  status = CASE
    WHEN picked.attempts >= picked.max_attempts THEN 'failed'::background_job_status
    ELSE 'pending'::background_job_status
  END,
  updated_at = NOW(),
  last_error = $2,
  lease_until = NULL,
  locked_by = NULL,
  locked_at = NULL,
  run_at = CASE
    WHEN picked.attempts >= picked.max_attempts THEN j.run_at
    ELSE NOW()
  END
FROM picked
WHERE j.id = picked.id
RETURNING
  j.id,
  j.status::text AS status,
  j.max_attempts;
"""

FIND_ORPHANED_PENDING_JOBS_SQL = """
SELECT id, max_attempts FROM background_jobs
WHERE status = 'pending'::background_job_status
  AND run_at <= NOW() - make_interval(secs => $2::double precision)
ORDER BY run_at ASC LIMIT $1::int;
"""


class PostgresJobRepository:
    def __init__(self, pool: PostgresPool) -> None:
        self._pool = pool

    async def insert_pending(self, input_data: InsertPendingJobInput) -> str | None:
        payload_json = json.dumps(input_data.payload or {})
        res = await self._pool.query(
            INSERT_PENDING_JOB_SQL,
            [
                input_data.queue_name,
                input_data.job_name,
                input_data.dedupe_key,
                payload_json,
                input_data.run_at,
                input_data.max_attempts,
            ],
        )
        if not res.rows:
            return None
        return str(res.rows[0]["id"])

    async def claim_by_id(self, input_data: ClaimJobInput) -> BackgroundJobRow | None:
        res = await self._pool.query(
            CLAIM_JOB_BY_ID_SQL,
            [input_data.id, input_data.worker_id, input_data.lease_sec],
        )
        if not res.rows:
            return None
        row = res.rows[0]
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception as exc:
                logger.debug("Failed parsing payload JSON: %s", exc)
        return BackgroundJobRow(
            id=str(row["id"]),
            queue_name=row["queue_name"],
            job_name=row["job_name"],
            payload=payload,
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
        )

    async def get_status(self, job_id: str) -> BackgroundJobStatus | None:
        res = await self._pool.query(GET_JOB_STATUS_SQL, [job_id])
        if not res.rows:
            return None
        return res.rows[0]["status"]  # type: ignore[no-any-return]

    async def mark_completed(self, job_id: str) -> None:
        await self._pool.query(MARK_JOB_COMPLETED_SQL, [job_id])

    async def mark_failed(
        self,
        job_id: str,
        error: Exception | str,
        requeue_at: datetime | None = None,
    ) -> None:
        msg = str(error)
        if requeue_at:
            await self._pool.query(REQUEUE_JOB_FOR_RETRY_SQL, [job_id, msg, requeue_at])
        else:
            await self._pool.query(MARK_JOB_FAILED_SQL, [job_id, msg, None])

    async def reclaim_expired_leases(self, limit: int = 50) -> list[ReclaimedLeaseRow]:
        res = await self._pool.query(
            RECLAIM_EXPIRED_LEASES_SQL,
            [limit, BACKGROUND_JOBS_LEASE_EXPIRED_MESSAGE],
        )
        return [
            ReclaimedLeaseRow(
                id=str(r["id"]),
                status=r["status"],
                max_attempts=r["max_attempts"],
            )
            for r in res.rows
        ]

    async def find_orphaned_pending_jobs(
        self, limit: int = 50, min_age_sec: float = 60.0
    ) -> list[dict[str, Any]]:
        res = await self._pool.query(
            FIND_ORPHANED_PENDING_JOBS_SQL, [limit, min_age_sec]
        )
        return [
            {"id": str(r["id"]), "maxAttempts": r["max_attempts"]} for r in res.rows
        ]
