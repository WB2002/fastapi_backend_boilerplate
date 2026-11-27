from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.conformance.conformance_harness_auth_guard import (
    require_conformance_harness_token,
)
from app.core.rate_limiter import (
    RateLimitAlgorithm,
    RateLimitIdentifier,
    RateLimitMethodConfig,
    rate_limit,
)

router = APIRouter(
    tags=["Conformance Rate Limit"],
    dependencies=[Depends(require_conformance_harness_token)],
)


@router.get("/conformance/rate-limit/user")
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


@router.get("/conformance/rate-limit/fingerprint")
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


@router.get("/conformance/rate-limit/guest")
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
