from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.conformance.conformance_harness_auth_guard import (
    require_conformance_harness_token,
)
from app.conformance.dto import ConformanceDeadlineQueryDto
from app.core.request_deadline import remaining_ms, throw_if_aborted

router = APIRouter(
    tags=["Conformance Deadline"],
    dependencies=[Depends(require_conformance_harness_token)],
)


@router.get("/conformance/deadline/slow")
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
