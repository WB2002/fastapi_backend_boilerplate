from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.common.decorators import public
from app.conformance.dto import ConformancePriorityQueryDto
from app.core.overload import ApiPriorityLevel, api_priority
from app.core.request_deadline import throw_if_aborted

router = APIRouter(prefix="/conformance/priority", tags=["Conformance Priority"])


async def _hold(hold_ms: int) -> None:
    if hold_ms > 0:
        await asyncio.sleep(hold_ms / 1000.0)
    throw_if_aborted()


@router.get("/critical")
@public
@api_priority(ApiPriorityLevel.CRITICAL)
async def critical(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "critical"}


@router.get("/high")
@public
@api_priority(ApiPriorityLevel.HIGH)
async def high(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "high"}


@router.get("/normal")
@public
@api_priority(ApiPriorityLevel.NORMAL)
async def normal(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "normal"}


@router.get("/low")
@public
@api_priority(ApiPriorityLevel.LOW)
async def low(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "low"}
