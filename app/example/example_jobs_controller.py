from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.common.decorators import public
from app.core.background_jobs.job_enqueue_service import JobEnqueueService
from app.core.background_jobs.types import EnqueueJobInput
from app.example.dto import EnqueueEchoJobDto
from app.example.jobs.example_echo_job_handler import EXAMPLE_ECHO_JOB_NAME

_ROUTE_VALIDATION = BaseModel

router = APIRouter(tags=["Example Jobs"])

_job_enqueue_service: JobEnqueueService | None = None


def set_job_enqueue_service(service: JobEnqueueService | None) -> None:
    global _job_enqueue_service
    _job_enqueue_service = service


def get_job_enqueue_service() -> JobEnqueueService:
    if _job_enqueue_service is None:
        raise RuntimeError("JobEnqueueService not configured")
    return _job_enqueue_service


@router.post("/example/jobs/echo", status_code=status.HTTP_201_CREATED)
@public
async def enqueue_echo(
    dto: EnqueueEchoJobDto,
    service: Annotated[JobEnqueueService, Depends(get_job_enqueue_service)],
) -> dict[str, Any]:
    res = await service.enqueue(
        EnqueueJobInput(
            job_name=EXAMPLE_ECHO_JOB_NAME,
            payload={"message": dto.message},
            dedupe_key=dto.dedupeKey,
        )
    )
    return {
        "jobRowId": res.job_row_id,
        "enqueued": res.enqueued,
        "deduplicated": res.deduplicated,
    }
