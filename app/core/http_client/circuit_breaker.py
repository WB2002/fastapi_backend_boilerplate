from __future__ import annotations

import time
from typing import Literal

from app.core.http_client.types import CircuitBreakerPolicy, CircuitOpenError

CircuitState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(self, policy: CircuitBreakerPolicy) -> None:
        self.failure_threshold = max(1, policy.failure_threshold)
        self.reset_timeout_ms = max(1, policy.reset_timeout_ms)
        self.half_open_max_attempts = max(1, policy.half_open_max_attempts)

        self.state: CircuitState = "closed"
        self.failures = 0
        self.opened_at_ms = 0
        self.half_open_attempts = 0

    def assert_closed(self) -> None:
        now = int(time.time() * 1000)
        if self.state == "open":
            if now - self.opened_at_ms >= self.reset_timeout_ms:
                self.state = "half_open"
                self.half_open_attempts = 0
            else:
                raise CircuitOpenError("Circuit breaker is open")

        if self.state == "half_open":
            if self.half_open_attempts >= self.half_open_max_attempts:
                raise CircuitOpenError("Circuit breaker half-open probe limit reached")
            self.half_open_attempts += 1

    def record_success(self) -> None:
        self.failures = 0
        self.half_open_attempts = 0
        self.state = "closed"

    def record_failure(self) -> None:
        now = int(time.time() * 1000)
        if self.state == "half_open":
            self.state = "open"
            self.opened_at_ms = now
            self.half_open_attempts = 0
        elif self.state == "closed":
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.state = "open"
                self.opened_at_ms = now
                self.half_open_attempts = 0
