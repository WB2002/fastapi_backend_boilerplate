from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

from celery import Celery

from app.common.config.parsers import parse_positive_int
from app.common.postgres.postgres_pool import PostgresPool
from app.core.background_jobs.constants import (
    BACKGROUND_JOBS_DEFAULT_QUEUE,
    BACKGROUND_JOBS_RUN_JOB_NAME,
)
from app.core.background_jobs.handlers import JobHandlerRegistry
from app.core.background_jobs.job_processor import BackgroundJobsRunProcessor
from app.core.background_jobs.repository import PostgresJobRepository
from app.example.jobs.example_echo_job_handler import (
    EXAMPLE_ECHO_JOB_NAME,
    ExampleEchoJobHandler,
)

DEFAULT_REDIS_HOST = "localhost"
DEFAULT_REDIS_PORT = 6379


def resolve_celery_broker_url(
    env: Mapping[str, str | None] | None = None,
) -> str:
    """Resolve Redis broker URL from environment variables."""
    target = os.environ if env is None else env
    redis_url = target.get("REDIS_URL")
    if redis_url is not None and redis_url.strip():
        return redis_url.strip()

    raw_host = target.get("REDIS_HOST")
    host = (
        raw_host.strip()
        if raw_host is not None and raw_host.strip()
        else DEFAULT_REDIS_HOST
    )
    port = parse_positive_int(target.get("REDIS_PORT"), DEFAULT_REDIS_PORT)
    return f"redis://{host}:{port}/0"


class WorkerRuntimeContext:
    """Context holding async dependencies for execution inside Celery workers."""

    def __init__(self, env: Mapping[str, str | None] | None = None) -> None:
        self.pool = PostgresPool(env=env)
        self.repo = PostgresJobRepository(self.pool)
        self.registry = JobHandlerRegistry()
        self.processor = BackgroundJobsRunProcessor(self.repo, self.registry)
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        self.registry.register(EXAMPLE_ECHO_JOB_NAME, ExampleEchoJobHandler())

    async def process_job(self, job_data: dict[str, Any] | str) -> None:
        await self.processor.process(job_data)

    async def close(self) -> None:
        await self.pool.close()


_worker_context: WorkerRuntimeContext | None = None


def get_worker_context(
    env: Mapping[str, str | None] | None = None,
) -> WorkerRuntimeContext:
    global _worker_context
    if _worker_context is None:
        _worker_context = WorkerRuntimeContext(env=env)
    return _worker_context


def create_celery_app(
    env: Mapping[str, str | None] | None = None,
    broker_url: str | None = None,
) -> Celery:
    """Factory creating a Celery instance configured for Redis broker."""
    resolved_broker = broker_url or resolve_celery_broker_url(env)
    app = Celery("fastapi_backend_boilerplate", broker=resolved_broker)

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_default_queue=BACKGROUND_JOBS_DEFAULT_QUEUE,
        task_queues={
            BACKGROUND_JOBS_DEFAULT_QUEUE: {
                "exchange": BACKGROUND_JOBS_DEFAULT_QUEUE,
                "routing_key": BACKGROUND_JOBS_DEFAULT_QUEUE,
            },
            "celery": {
                "exchange": "celery",
                "routing_key": "celery",
            },
            "background-jobs": {
                "exchange": "background-jobs",
                "routing_key": "background-jobs",
            },
        },
    )

    def run_job(self: Any, job_data: dict[str, Any] | str) -> None:
        """Worker task claiming and executing a row from background_jobs."""
        ctx = get_worker_context()

        async def _run() -> None:
            await ctx.process_job(job_data)

        try:
            asyncio.run(_run())
        except Exception as exc:
            # Re-raise so Celery logs the exception
            raise exc

    if BACKGROUND_JOBS_RUN_JOB_NAME not in app.tasks:
        task_fn: Any = app.task(
            name=BACKGROUND_JOBS_RUN_JOB_NAME,
            bind=True,
            max_retries=5,
        )
        task_fn(run_job)

    # Also register the dot-notation alias for compatibility
    if "background_jobs.run_job" not in app.tasks:
        alias_fn: Any = app.task(
            name="background_jobs.run_job",
            bind=True,
            max_retries=5,
        )
        alias_fn(run_job)

    return app


celery_app: Celery = create_celery_app()

__all__ = [
    "WorkerRuntimeContext",
    "celery_app",
    "create_celery_app",
    "get_worker_context",
    "resolve_celery_broker_url",
]
