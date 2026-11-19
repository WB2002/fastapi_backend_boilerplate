from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

from app.common.constants import (
    IS_PUBLIC_KEY,
    SKIP_COUNTRY_ACCESS_KEY,
    SKIP_INBOUND_BULKHEAD_KEY,
    SKIP_RATE_LIMIT_KEY,
    SKIP_REQUEST_DEADLINE_KEY,
    SKIP_TRACING_KEY,
)


def _set_flag[F: Callable[..., Any] | type[Any]](
    key: str,
    target: F | None = None,
) -> Any:
    def _decorator(obj: F) -> F:
        setattr(obj, key, True)
        return obj

    if target is not None:
        return _decorator(target)
    return _decorator


@overload
def public[F: Callable[..., Any] | type[Any]](target: F) -> F: ...


@overload
def public[F: Callable[..., Any] | type[Any]](
    target: None = None,
) -> Callable[[F], F]: ...


def public[F: Callable[..., Any] | type[Any]](
    target: F | None = None,
) -> F | Callable[[F], F]:
    """Mark an endpoint handler or controller as publicly accessible."""
    flagged: F | Callable[[F], F] = _set_flag(IS_PUBLIC_KEY, target)
    return flagged


def is_public_endpoint(endpoint: object) -> bool:
    """Return True if an endpoint callable has been decorated with @public."""
    return bool(getattr(endpoint, IS_PUBLIC_KEY, False))


def skip_rate_limit[F: Callable[..., Any] | type[Any]](
    target: F | None = None,
) -> Any:
    """Mark a route handler or controller to bypass RateLimitGuard."""
    return _set_flag(SKIP_RATE_LIMIT_KEY, target)


def should_skip_rate_limit(endpoint: object, controller: object | None = None) -> bool:
    return bool(
        getattr(endpoint, SKIP_RATE_LIMIT_KEY, False)
        or (controller is not None and getattr(controller, SKIP_RATE_LIMIT_KEY, False))
    )


def skip_request_deadline[F: Callable[..., Any] | type[Any]](
    target: F | None = None,
) -> Any:
    """Mark a route handler or controller to bypass RequestDeadlineMiddleware."""
    return _set_flag(SKIP_REQUEST_DEADLINE_KEY, target)


def should_skip_request_deadline(
    endpoint: object,
    controller: object | None = None,
) -> bool:
    return bool(
        getattr(endpoint, SKIP_REQUEST_DEADLINE_KEY, False)
        or (
            controller is not None
            and getattr(controller, SKIP_REQUEST_DEADLINE_KEY, False)
        )
    )


def skip_country_access[F: Callable[..., Any] | type[Any]](
    target: F | None = None,
) -> Any:
    """Mark a route handler or controller to bypass CountryAccessGuard."""
    return _set_flag(SKIP_COUNTRY_ACCESS_KEY, target)


def should_skip_country_access(
    endpoint: object,
    controller: object | None = None,
) -> bool:
    return bool(
        getattr(endpoint, SKIP_COUNTRY_ACCESS_KEY, False)
        or (
            controller is not None
            and getattr(controller, SKIP_COUNTRY_ACCESS_KEY, False)
        )
    )


def skip_inbound_bulkhead[F: Callable[..., Any] | type[Any]](
    target: F | None = None,
) -> Any:
    """Mark a route handler or controller to bypass InboundPriorityBulkheadGuard."""
    return _set_flag(SKIP_INBOUND_BULKHEAD_KEY, target)


def should_skip_inbound_bulkhead(
    endpoint: object,
    controller: object | None = None,
) -> bool:
    return bool(
        getattr(endpoint, SKIP_INBOUND_BULKHEAD_KEY, False)
        or (
            controller is not None
            and getattr(controller, SKIP_INBOUND_BULKHEAD_KEY, False)
        )
    )


def skip_tracing[F: Callable[..., Any] | type[Any]](
    target: F | None = None,
) -> Any:
    """Mark a route handler or controller to bypass OTel TracingInterceptor."""
    return _set_flag(SKIP_TRACING_KEY, target)


Public = public
SkipRateLimit = skip_rate_limit
SkipRequestDeadline = skip_request_deadline
SkipCountryAccess = skip_country_access
SkipInboundBulkhead = skip_inbound_bulkhead
SkipTracing = skip_tracing

__all__ = [
    "IS_PUBLIC_KEY",
    "SKIP_COUNTRY_ACCESS_KEY",
    "SKIP_INBOUND_BULKHEAD_KEY",
    "SKIP_RATE_LIMIT_KEY",
    "SKIP_REQUEST_DEADLINE_KEY",
    "SKIP_TRACING_KEY",
    "Public",
    "SkipCountryAccess",
    "SkipInboundBulkhead",
    "SkipRateLimit",
    "SkipRequestDeadline",
    "SkipTracing",
    "is_public_endpoint",
    "public",
    "should_skip_country_access",
    "should_skip_inbound_bulkhead",
    "should_skip_rate_limit",
    "should_skip_request_deadline",
    "skip_country_access",
    "skip_inbound_bulkhead",
    "skip_rate_limit",
    "skip_request_deadline",
    "skip_tracing",
]
