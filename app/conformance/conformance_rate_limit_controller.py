from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.common.decorators import public
from app.core.rate_limiter import (
    RateLimitAlgorithm,
    RateLimitIdentifier,
    RateLimitMethodConfig,
    rate_limit,
)

router = APIRouter(prefix="/conformance/rate-limit", tags=["Conformance Rate Limit"])


@router.get("/user")
@rate_limit(
    RateLimitMethodConfig(
        name="conformance-user",
        identifier=RateLimitIdentifier.USER_ID,
        duration=60_000,
        limit=3,
    ),
    algorithm=RateLimitAlgorithm.FIXED_WINDOW_COUNTER,
)
def user() -> dict[str, Any]:
    return {"identifierKind": "user"}


@router.get("/fingerprint")
@public
@rate_limit(
    RateLimitMethodConfig(
        name="conformance-fingerprint",
        identifier=RateLimitIdentifier.DEVICE_FINGERPRINT,
        duration=60_000,
        limit=3,
    ),
    algorithm=RateLimitAlgorithm.FIXED_WINDOW_COUNTER,
)
def fingerprint() -> dict[str, Any]:
    return {"identifierKind": "fingerprint"}


@router.get("/guest")
@public
@rate_limit(
    RateLimitMethodConfig(
        name="conformance-guest",
        identifier=RateLimitIdentifier.GUEST_ID,
        duration=60_000,
        limit=3,
    ),
    algorithm=RateLimitAlgorithm.FIXED_WINDOW_COUNTER,
)
def guest() -> dict[str, Any]:
    return {"identifierKind": "guest"}
