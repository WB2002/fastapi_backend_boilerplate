from __future__ import annotations

import os
from collections.abc import Mapping

from app.core.rate_limiter.rate_limit_guard import (
    GLOBAL_IP_METHOD,
    RateLimitMethodConfig,
)
from app.core.rate_limiter.stores import RateLimitIdentifier

_DEFAULT_DEV_GLOBAL_IP_LIMIT = 600
_DEFAULT_PROD_GLOBAL_IP_LIMIT = 10


def resolve_default_rate_limit_methods(
    env: Mapping[str, str | None] | None = None,
) -> tuple[RateLimitMethodConfig, ...]:
    """Resolve global IP rate-limit policy (NestJS parity)."""
    target = os.environ if env is None else env
    node_env = (target.get("NODE_ENV") or "development").strip().lower()
    raw_limit = (target.get("RATE_LIMIT_GLOBAL_IP_LIMIT") or "").strip()
    configured: int | None = None
    if raw_limit.isdigit():
        parsed = int(raw_limit)
        if parsed > 0:
            configured = parsed
    limit = configured or (
        _DEFAULT_PROD_GLOBAL_IP_LIMIT
        if node_env == "production"
        else _DEFAULT_DEV_GLOBAL_IP_LIMIT
    )
    method = RateLimitMethodConfig(
        name=GLOBAL_IP_METHOD.name,
        identifier=RateLimitIdentifier.IP,
        duration=GLOBAL_IP_METHOD.duration,
        limit=limit,
        cost=GLOBAL_IP_METHOD.cost,
        key_scope=GLOBAL_IP_METHOD.key_scope,
    )
    return (method,)


__all__ = ["resolve_default_rate_limit_methods"]
