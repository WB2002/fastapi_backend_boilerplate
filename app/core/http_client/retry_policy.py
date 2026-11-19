from __future__ import annotations

import random
from typing import Any

from app.core.http_client.types import (
    HttpClientError,
    HttpNetworkError,
    HttpRequest,
    HttpTimeoutError,
    RetryPolicy,
)


def compute_backoff_delay(
    strategy: str,
    attempt: int,
    base_delay_ms: int,
    max_delay_ms: int,
    factor: float = 2.0,
) -> int:
    """Calculate raw backoff delay in ms before jitter."""
    att = max(1, attempt)
    if strategy == "fixed":
        return max(0, base_delay_ms)
    elif strategy == "linear":
        return min(base_delay_ms * att, max_delay_ms)
    elif strategy == "exponential":
        return min(int(base_delay_ms * (factor ** (att - 1))), max_delay_ms)
    return base_delay_ms


def apply_jitter(
    jitter_type: str,
    delay_ms: int,
    base_delay_ms: int,
    max_delay_ms: int,
    previous_delay_ms: int | None = None,
) -> int:
    """Apply jitter to the raw delay."""
    if delay_ms <= 0:
        return 0
    if jitter_type == "none":
        return delay_ms
    elif jitter_type == "full":
        return int(random.random() * delay_ms)  # noqa: S311
    elif jitter_type == "equal":
        half = delay_ms / 2.0
        return int(half + random.random() * half)  # noqa: S311
    elif jitter_type == "decorrelated":
        prev = previous_delay_ms if previous_delay_ms is not None else base_delay_ms
        return min(
            max_delay_ms,
            int(base_delay_ms + random.random() * prev * 3),  # noqa: S311
        )
    return delay_ms


def calculate_retry_delay(
    policy: RetryPolicy,
    attempt: int,
    previous_delay_ms: int | None = None,
) -> int:
    raw = compute_backoff_delay(
        policy.strategy,
        attempt,
        policy.base_delay_ms,
        policy.max_delay_ms,
        policy.factor,
    )
    return apply_jitter(
        policy.jitter,
        raw,
        policy.base_delay_ms,
        policy.max_delay_ms,
        previous_delay_ms,
    )


def default_is_transient(error_or_status: Any) -> bool:
    """Status in [408, 425, 429, 500, 502, 503, 504] or network/timeout errors."""
    if isinstance(error_or_status, int):
        return error_or_status in (408, 425, 429, 500, 502, 503, 504)
    if isinstance(
        error_or_status,
        (HttpTimeoutError, HttpNetworkError, TimeoutError, ConnectionError),
    ):
        return True
    if isinstance(error_or_status, HttpClientError):
        if error_or_status.status_code is not None:
            return error_or_status.status_code in (
                408,
                425,
                429,
                500,
                502,
                503,
                504,
            )
    return False


def default_can_retry(request: HttpRequest) -> bool:
    """Idempotent methods or requests with explicit idempotency."""
    return request.method.upper() in {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
