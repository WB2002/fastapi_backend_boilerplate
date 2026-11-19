from __future__ import annotations

import math
import random
from collections.abc import Callable

from app.core.cache.types import CacheTtl


def apply_ttl_jitter(
    base: CacheTtl,
    ratio: float,
    rand_fn: Callable[[], float] | None = None,
) -> CacheTtl:
    """Apply proportional jitter to soft and hard cache TTL values."""
    if ratio <= 0:
        return base

    fn = rand_fn or random.random
    soft_jitter = math.floor(fn() * ratio * base.soft_ttl_ms)
    hard_jitter = math.floor(fn() * ratio * base.hard_ttl_ms)

    soft_ttl_ms = base.soft_ttl_ms + soft_jitter
    hard_ttl_ms = base.hard_ttl_ms + hard_jitter

    if soft_ttl_ms <= 0 or hard_ttl_ms <= 0:
        raise ValueError("Cache TTL values must be > 0 after jitter")
    if soft_ttl_ms > hard_ttl_ms:
        raise ValueError("softTtlMs must be <= hardTtlMs")

    return CacheTtl(soft_ttl_ms=soft_ttl_ms, hard_ttl_ms=hard_ttl_ms)
