from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.common.decorators import public
from app.conformance.dto import ConformanceDeadlineQueryDto
from app.core.request_deadline import remaining_ms, throw_if_aborted

router = APIRouter(prefix="/conformance/deadline", tags=["Conformance Deadline"])


@router.get("/slow")
@public
async def slow(
    query: Annotated[ConformanceDeadlineQueryDto, Depends()],
) -> dict[str, Any]:
    start_time = time.time()
    delay_ms = query.delayMs
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    throw_if_aborted()
    elapsed_ms = int((time.time() - start_time) * 1000)

    return {
        "completedAfterMs": max(0, elapsed_ms),
        "remainingBudgetMs": remaining_ms(),
    }
