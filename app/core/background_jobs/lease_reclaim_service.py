from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.background_jobs.job_enqueue_service import JobEnqueueService
from app.core.background_jobs.repository import PostgresJobRepository

logger = logging.getLogger("JobLeaseReclaimService")


class JobLeaseReclaimService:
    def __init__(
        self,
        repository: PostgresJobRepository,
        enqueue: JobEnqueueService,
        config: Any = None,
        *,
        enabled: bool = True,
        lease_reclaim_enabled: bool = True,
        lease_reclaim_interval_ms: int = 30_000,
        lease_reclaim_batch_size: int = 25,
        orphan_min_age_sec: float = 30.0,
    ) -> None:
        self._repo = repository
        self._enqueue = enqueue

        if config is not None:
            self._enabled = getattr(config, "enabled", enabled)
            self._lease_reclaim_enabled = getattr(
                config, "lease_reclaim_enabled", lease_reclaim_enabled
            )
            self._interval_sec = (
                getattr(config, "lease_reclaim_interval_ms", lease_reclaim_interval_ms)
                / 1000.0
            )
            self._batch_size = getattr(
                config, "lease_reclaim_batch_size", lease_reclaim_batch_size
            )
            self._orphan_min_age_sec = float(
                getattr(config, "orphan_min_age_sec", orphan_min_age_sec)
            )
        else:
            self._enabled = enabled
            self._lease_reclaim_enabled = lease_reclaim_enabled
            self._interval_sec = lease_reclaim_interval_ms / 1000.0
            self._batch_size = lease_reclaim_batch_size
            self._orphan_min_age_sec = float(orphan_min_age_sec)

        self._task: asyncio.Task[None] | None = None
        self._is_tick_in_flight = False

    def has_active_timer(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self._enabled or not self._lease_reclaim_enabled:
            return
        self.stop()
        self._task = asyncio.create_task(self._run_loop())

    def on_module_init(self) -> None:
        self.start()

    def on_module_destroy(self) -> None:
        self.stop()

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        try:
            await self.tick()
            while True:
                await asyncio.sleep(self._interval_sec)
                await self.tick()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Error in lease reclaim loop: %s", exc)

    async def tick(self) -> None:
        if self._is_tick_in_flight:
            return
        self._is_tick_in_flight = True
        try:
            rows = await self._repo.reclaim_expired_leases(self._batch_size)
            for row in rows:
                if row.status == "pending":
                    await self._enqueue.wake_job_row(row.id, row.max_attempts)
            if rows:
                logger.info("Reclaimed %d expired background job lease(s)", len(rows))

            orphaned_rows = await self._repo.find_orphaned_pending_jobs(
                self._batch_size, self._orphan_min_age_sec
            )
            successfully_woken_count = 0
            for row_dict in orphaned_rows:
                try:
                    await self._enqueue.wake_job_row(
                        row_dict["id"], row_dict.get("maxAttempts", 5)
                    )
                    successfully_woken_count += 1
                except Exception as err:
                    logger.warning(
                        "Failed to wake orphaned job %s: %s",
                        row_dict.get("id"),
                        err,
                    )
            if successfully_woken_count > 0:
                logger.info(
                    "Woke %d orphaned pending background job(s)",
                    successfully_woken_count,
                )
        except Exception as err:
            logger.warning("Lease reclaim tick failed: %s", err)
        finally:
            self._is_tick_in_flight = False
