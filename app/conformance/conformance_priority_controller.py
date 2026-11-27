from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.conformance.conformance_harness_auth_guard import (
    require_conformance_harness_token,
)
from app.conformance.dto import ConformancePriorityQueryDto
from app.core.overload import ApiPriorityLevel, api_priority
from app.core.request_deadline import throw_if_aborted

router = APIRouter(
    tags=["Conformance Priority"],
    dependencies=[Depends(require_conformance_harness_token)],
)


async def _hold(hold_ms: int) -> None:
    if hold_ms > 0:
        await asyncio.sleep(hold_ms / 1000.0)
    throw_if_aborted()


@router.get("/conformance/priority/critical")
@api_priority(ApiPriorityLevel.CRITICAL)
async def critical(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "critical"}


@router.get("/conformance/priority/high")
@api_priority(ApiPriorityLevel.HIGH)
async def high(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "high"}


@router.get("/conformance/priority/normal")
@api_priority(ApiPriorityLevel.NORMAL)
async def normal(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "normal"}


@router.get("/conformance/priority/low")
@api_priority(ApiPriorityLevel.LOW)
async def low(
    query: Annotated[ConformancePriorityQueryDto, Depends()],
) -> dict[str, Any]:
    await _hold(query.holdMs)
    return {"priority": "low"}
