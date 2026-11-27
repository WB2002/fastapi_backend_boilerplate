from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status


class ConformanceHarnessAuthGuard:
    """Bearer token guard for conformance harness routes."""

    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token


def _expected_harness_token(env: Mapping[str, str | None] | None = None) -> str:
    target = os.environ if env is None else env
    return (target.get("CONFORMANCE_HARNESS_TOKEN") or "").strip()


def _read_bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing conformance harness token",
        )
    token = header[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing conformance harness token",
        )
    return token


def _tokens_match(expected: str, provided: str) -> bool:
    return hmac.compare_digest(
        expected.encode("utf-8"),
        provided.encode("utf-8"),
    )


async def require_conformance_harness_token(request: Request) -> None:
    env: Mapping[str, str | None] = getattr(request.app.state, "env", os.environ)
    expected = _expected_harness_token(env)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Conformance harness is not configured",
        )
    provided = _read_bearer_token(request)
    if not _tokens_match(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid conformance harness token",
        )


ConformanceHarnessTokenDep = Annotated[None, Depends(require_conformance_harness_token)]

__all__ = [
    "ConformanceHarnessAuthGuard",
    "ConformanceHarnessTokenDep",
    "require_conformance_harness_token",
]
