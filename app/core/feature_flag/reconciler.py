from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.core.feature_flag.constants import DEFAULT_FEATURE_FLAG_RECONCILE_MS

logger = logging.getLogger("FeatureFlagReconciler")


class FeatureFlagReconciler:
    def __init__(
        self,
        reconcile_fn: Callable[[], asyncio.Future[None] | Any],
        interval_ms: int = DEFAULT_FEATURE_FLAG_RECONCILE_MS,
    ) -> None:
        self._reconcile_fn = reconcile_fn
        self._interval_ms = interval_ms
        self._is_running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True

        async def _loop() -> None:
            interval_sec = max(1.0, self._interval_ms / 1000.0)
            while self._is_running:
                try:
                    await asyncio.sleep(interval_sec)
                    if self._is_running:
                        res = self._reconcile_fn()
                        if asyncio.iscoroutine(res):
                            await res
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Reconciliation error: %s", exc)

        self._task = asyncio.create_task(_loop())

    def stop(self) -> None:
        self._is_running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
