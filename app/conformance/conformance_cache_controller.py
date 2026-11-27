from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.conformance.conformance_cache_service import ConformanceCacheService
from app.conformance.conformance_harness_auth_guard import (
    JwtAuthGuard,
    require_conformance_harness_token,
)
from app.conformance.dto import ConformanceCacheRequestDto

_ROUTE_AUTH_GUARD = JwtAuthGuard
_ROUTE_VALIDATION = BaseModel

router = APIRouter(
    tags=["Conformance Cache"],
    dependencies=[Depends(require_conformance_harness_token)],
)

_cache_service: ConformanceCacheService | None = None


def set_conformance_cache_service(service: ConformanceCacheService | None) -> None:
    global _cache_service
    _cache_service = service


def get_conformance_cache_service() -> ConformanceCacheService:
    if _cache_service is None:
        raise RuntimeError("ConformanceCacheService not configured")
    return _cache_service


@router.post("/conformance/cache", status_code=status.HTTP_200_OK)
async def post(
    request: ConformanceCacheRequestDto,
    service: Annotated[ConformanceCacheService, Depends(get_conformance_cache_service)],
) -> dict[str, Any]:
    return await service.get_or_set(request)


@router.delete("/conformance/cache/{key}", status_code=status.HTTP_200_OK)
async def delete(
    key: str,
    service: Annotated[ConformanceCacheService, Depends(get_conformance_cache_service)],
) -> dict[str, bool]:
    await service.reset(key)
    return {"deleted": True}
