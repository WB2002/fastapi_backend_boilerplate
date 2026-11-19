from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.background_jobs.celery_app import (
    WorkerRuntimeContext,
    create_celery_app,
    resolve_celery_broker_url,
)
from app.core.background_jobs.constants import BACKGROUND_JOBS_RUN_JOB_NAME
from app.core.background_jobs.dispatcher import (
    CeleryJobProxy,
    CeleryQueueDispatcher,
)


def test_resolve_celery_broker_url_explicit_redis_url() -> None:
    env = {"REDIS_URL": "redis://user:pass@redis.custom.internal:6380/2"}
    expected = "redis://user:pass@redis.custom.internal:6380/2"
    assert resolve_celery_broker_url(env) == expected


def test_resolve_celery_broker_url_host_and_port() -> None:
    env = {"REDIS_HOST": "cache.service", "REDIS_PORT": "6385"}
    assert resolve_celery_broker_url(env) == "redis://cache.service:6385/0"


def test_resolve_celery_broker_url_defaults() -> None:
    assert resolve_celery_broker_url({}) == "redis://localhost:6379/0"


def test_create_celery_app_configuration() -> None:
    app = create_celery_app(broker_url="redis://localhost:6379/0")
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.enable_utc is True
    assert app.conf.timezone == "UTC"
    assert BACKGROUND_JOBS_RUN_JOB_NAME in app.tasks
    assert "background_jobs.run_job" in app.tasks


def test_celery_queue_dispatcher_dispatch() -> None:
    mock_celery = MagicMock()
    mock_celery.send_task = MagicMock(return_value="task-sent")

    dispatcher = CeleryQueueDispatcher(mock_celery, default_queue="default")
    res = dispatcher.dispatch(
        "background_jobs:run",
        {"jobRowId": "job-123"},
        queue="background-jobs",
    )

    assert res == "task-sent"
    mock_celery.send_task.assert_called_once_with(
        "background_jobs:run",
        args=[{"jobRowId": "job-123"}],
        task_id="job-123",
        queue="background-jobs",
        countdown=None,
    )


def test_celery_queue_dispatcher_add_alias() -> None:
    mock_celery = MagicMock()
    dispatcher = CeleryQueueDispatcher(mock_celery, default_queue="default")
    dispatcher.add("background_jobs:run", {"jobRowId": "job-456"})
    mock_celery.send_task.assert_called_once_with(
        "background_jobs:run",
        args=[{"jobRowId": "job-456"}],
        task_id="job-456",
        queue="default",
        countdown=None,
    )


def test_celery_job_proxy_states_and_remove() -> None:
    mock_celery = MagicMock()
    mock_res = MagicMock()
    mock_celery.AsyncResult = MagicMock(return_value=mock_res)
    mock_celery.control.revoke = MagicMock()

    proxy = CeleryJobProxy("job-123", mock_celery)

    mock_res.state = "PENDING"
    assert proxy.get_state() == "waiting"

    mock_res.state = "STARTED"
    assert proxy.get_state() == "active"

    mock_res.state = "SUCCESS"
    assert proxy.get_state() == "completed"

    mock_res.state = "FAILURE"
    assert proxy.get_state() == "failure"

    proxy.remove()
    mock_celery.control.revoke.assert_called_once_with("job-123", terminate=True)


@pytest.mark.asyncio
async def test_worker_runtime_context_process_job() -> None:
    with (
        patch("app.core.background_jobs.celery_app.PostgresPool") as mock_pool_cls,
        patch("app.core.background_jobs.celery_app.PostgresJobRepository"),
        patch(
            "app.core.background_jobs.celery_app.BackgroundJobsRunProcessor"
        ) as mock_proc_cls,
    ):
        mock_proc = MagicMock()
        mock_proc.process = AsyncMock(return_value=None)
        mock_proc_cls.return_value = mock_proc

        mock_pool = MagicMock()
        mock_pool.close = AsyncMock(return_value=None)
        mock_pool_cls.return_value = mock_pool

        ctx = WorkerRuntimeContext()
        assert ctx.registry.get("example.echo") is not None

        await ctx.process_job({"jobRowId": "uuid-1"})
        mock_proc.process.assert_awaited_once_with({"jobRowId": "uuid-1"})

        await ctx.close()
        mock_pool.close.assert_awaited_once()
