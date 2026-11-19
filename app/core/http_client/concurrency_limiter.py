from __future__ import annotations

import asyncio
import math
import os
from typing import Literal

from app.core.http_client.types import (
    AdaptiveConcurrencyTuning,
    AdaptiveLimitExceededError,
)


def scale_cluster_aware_limit(
    value: int, cluster_aware: bool, instance_count: int | None = None
) -> int:
    if not cluster_aware:
        return value
    if instance_count is None:
        raw = os.getenv("HTTP_CLIENT_INSTANCE_COUNT", "1")
        try:
            instance_count = max(1, int(raw))
        except ValueError:
            instance_count = 1
    return max(1, math.floor(value / max(1, instance_count)))


class FixedConcurrencyGate:
    def __init__(self, max_concurrent: int) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        self._in_flight += 1

    async def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self._semaphore.release()


class AdaptiveLimiter:
    def __init__(
        self,
        tuning: AdaptiveConcurrencyTuning,
        cluster_aware: bool = False,
    ) -> None:
        self.algorithm: Literal["aimd", "vegas", "gradient2"] = tuning.algorithm
        self.min_limit = max(1, tuning.min_limit)
        self.max_limit = max(
            self.min_limit,
            scale_cluster_aware_limit(tuning.max_limit, cluster_aware),
        )
        self.current_limit = max(
            self.min_limit, min(tuning.initial_limit, self.max_limit)
        )
        self.success_threshold = max(1, tuning.success_threshold)
        self.vegas_alpha = tuning.vegas_alpha
        self.vegas_beta = tuning.vegas_beta
        self.vegas_min_samples = tuning.vegas_min_samples
        self.gradient_min_samples = tuning.gradient_min_samples
        self.gradient_smoothing = tuning.gradient_smoothing

        self._in_flight = 0
        self._success_count = 0
        self._min_rtt_ms = float("inf")
        self._ema_rtt_ms = 0.0
        self._samples = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def acquire(self) -> None:
        if self._in_flight >= self.current_limit:
            raise AdaptiveLimitExceededError(
                f"Adaptive limit ({self.current_limit}) exceeded; "
                f"in-flight: {self._in_flight}"
            )
        self._in_flight += 1

    def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)

    def record_failure(self) -> None:
        self._success_count = 0
        if self.algorithm == "aimd":
            self.current_limit = max(
                self.min_limit, math.floor(self.current_limit * 0.5)
            )
        else:
            self.current_limit = max(self.min_limit, self.current_limit - 1)

    def record_success(self, rtt_ms: int) -> None:
        self._success_count += 1
        self._samples += 1
        self._min_rtt_ms = min(self._min_rtt_ms, float(rtt_ms))

        if self.algorithm == "aimd":
            if self._success_count >= self.success_threshold:
                self._success_count = 0
                self.current_limit = min(self.max_limit, self.current_limit + 1)
        elif self.algorithm == "vegas":
            if self._ema_rtt_ms == 0.0:
                self._ema_rtt_ms = float(rtt_ms)
            else:
                self._ema_rtt_ms = self._ema_rtt_ms * 0.9 + float(rtt_ms) * 0.1

            if self._samples >= self.vegas_min_samples and self._ema_rtt_ms > 0:
                queue = math.trunc(
                    (self.current_limit * (self._ema_rtt_ms - self._min_rtt_ms))
                    / self._ema_rtt_ms
                )
                if queue < self.vegas_alpha:
                    self.current_limit = min(self.max_limit, self.current_limit + 1)
                elif queue > self.vegas_beta:
                    self.current_limit = max(self.min_limit, self.current_limit - 1)
        elif self.algorithm == "gradient2":
            if self._ema_rtt_ms == 0.0:
                self._ema_rtt_ms = float(rtt_ms)
            else:
                self._ema_rtt_ms = (
                    self._ema_rtt_ms * (1.0 - self.gradient_smoothing)
                    + float(rtt_ms) * self.gradient_smoothing
                )

            if self._samples >= self.gradient_min_samples and self._ema_rtt_ms > 0:
                gradient = max(0.5, min(1.0, self._min_rtt_ms / self._ema_rtt_ms))
                target = math.floor(
                    self.current_limit * gradient + math.sqrt(self.current_limit)
                )
                self.current_limit = max(self.min_limit, min(self.max_limit, target))


class AdaptiveConcurrencyGate:
    def __init__(
        self,
        tuning: AdaptiveConcurrencyTuning,
        cluster_aware: bool = False,
    ) -> None:
        self._limiter = AdaptiveLimiter(tuning, cluster_aware)

    @property
    def in_flight(self) -> int:
        return self._limiter.in_flight

    async def acquire(self) -> None:
        self._limiter.acquire()

    async def release(self) -> None:
        self._limiter.release()

    def record_success(self, rtt_ms: int) -> None:
        self._limiter.record_success(rtt_ms)

    def record_failure(self) -> None:
        self._limiter.record_failure()
