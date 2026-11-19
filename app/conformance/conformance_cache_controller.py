from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.common.decorators import public
from app.conformance.conformance_cache_service import ConformanceCacheService
from app.conformance.dto import ConformanceCacheRequestDto

router = APIRouter(prefix="/conformance/cache", tags=["Conformance Cache"])

_cache_service: ConformanceCacheService | None = None


def set_conformance_cache_service(service: ConformanceCacheService | None) -> None:
    global _cache_service
    _cache_service = service


def get_conformance_cache_service() -> ConformanceCacheService:
    if _cache_service is None:
        raise RuntimeError("ConformanceCacheService not configured")
    return _cache_service


@router.post("", status_code=status.HTTP_200_OK)
@public
async def post(
    request: ConformanceCacheRequestDto,
    service: Annotated[ConformanceCacheService, Depends(get_conformance_cache_service)],
) -> dict[str, Any]:
    return await service.get_or_set(request)


@router.delete("/{key}", status_code=status.HTTP_200_OK)
@public
async def delete(
    key: str,
    service: Annotated[ConformanceCacheService, Depends(get_conformance_cache_service)],
) -> dict[str, bool]:
    await service.reset(key)
    return {"deleted": True}
