from __future__ import annotations

from app.core.rate_limiter.resolve_default_rate_limit_methods import (
    resolve_default_rate_limit_methods,
)
from app.core.rate_limiter.stores import RateLimitIdentifier


def test_resolve_default_rate_limit_respects_env_override() -> None:
    (method,) = resolve_default_rate_limit_methods(
        {"NODE_ENV": "test", "RATE_LIMIT_GLOBAL_IP_LIMIT": "100000"}
    )
    assert method.identifier == RateLimitIdentifier.IP
    assert method.limit == 100_000


def test_resolve_default_rate_limit_prod_default() -> None:
    (method,) = resolve_default_rate_limit_methods({"NODE_ENV": "production"})
    assert method.limit == 10


def test_resolve_default_rate_limit_dev_default() -> None:
    (method,) = resolve_default_rate_limit_methods({"NODE_ENV": "development"})
    assert method.limit == 600
