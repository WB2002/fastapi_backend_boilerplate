from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.background_jobs.constants import BACKGROUND_JOBS_RUN_JOB_NAME
from app.core.background_jobs.handlers import JobHandlerRegistry
from app.core.background_jobs.job_enqueue_service import JobEnqueueService
from app.core.background_jobs.job_processor import BackgroundJobsRunProcessor
from app.core.background_jobs.lease_reclaim_service import JobLeaseReclaimService
from app.core.background_jobs.types import (
    BackgroundJobRow,
    EnqueueJobInput,
    ReclaimedLeaseRow,
)
from app.example.dto import EnqueueEchoJobDto
from app.example.example_jobs_controller import enqueue_echo
from app.example.jobs.example_echo_job_handler import ExampleEchoJobHandler


@pytest.mark.asyncio
async def test_job_enqueue_service_adds_dispatcher_event_after_insert() -> None:
    repo = MagicMock()
    repo.insert_pending = AsyncMock(return_value="job-1")
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(return_value=None)

    service = JobEnqueueService(repo, dispatcher, "default")
    outcome = await service.enqueue(
        EnqueueJobInput(
            job_name="example.echo",
            payload={"message": "hi"},
        )
    )

    assert outcome.enqueued is True
    assert outcome.job_row_id == "job-1"
    assert outcome.deduplicated is False
    dispatcher.dispatch.assert_awaited_once_with(
        BACKGROUND_JOBS_RUN_JOB_NAME,
        {"jobRowId": "job-1"},
    )


@pytest.mark.asyncio
async def test_job_enqueue_service_returns_deduplicated_when_conflict() -> None:
    repo = MagicMock()
    repo.insert_pending = AsyncMock(return_value=None)
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()

    service = JobEnqueueService(repo, dispatcher, "default")
    outcome = await service.enqueue(
        EnqueueJobInput(
            job_name="example.echo",
            payload={},
            dedupe_key="k",
        )
    )

    assert outcome.deduplicated is True
    assert outcome.enqueued is False
    dispatcher.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_job_enqueue_service_returns_enqueued_when_dispatcher_fails() -> None:
    repo = MagicMock()
    repo.insert_pending = AsyncMock(return_value="job-1")
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("Redis connection lost"))

    service = JobEnqueueService(repo, dispatcher, "default")
    outcome = await service.enqueue(
        EnqueueJobInput(
            job_name="example.echo",
            payload={"message": "hi"},
        )
    )

    assert outcome.enqueued is True
    assert outcome.job_row_id == "job-1"
    assert outcome.deduplicated is False


@pytest.mark.asyncio
async def test_wake_job_row_removes_completed_and_readds() -> None:
    repo = MagicMock()
    mock_job = MagicMock()
    mock_job.get_state = AsyncMock(return_value="completed")
    mock_job.remove = AsyncMock()

    dispatcher = MagicMock()
    dispatcher.get_job = AsyncMock(return_value=mock_job)
    dispatcher.dispatch = AsyncMock()

    service = JobEnqueueService(repo, dispatcher, "default")
    await service.wake_job_row("00000000-0000-4000-8000-000000000001", 5)

    mock_job.remove.assert_awaited_once()
    dispatcher.dispatch.assert_awaited_once_with(
        BACKGROUND_JOBS_RUN_JOB_NAME,
        {"jobRowId": "00000000-0000-4000-8000-000000000001"},
    )


@pytest.mark.asyncio
async def test_wake_job_row_skips_when_active_or_waiting() -> None:
    repo = MagicMock()
    mock_job = MagicMock()
    mock_job.get_state = AsyncMock(return_value="waiting")
    mock_job.remove = AsyncMock()

    dispatcher = MagicMock()
    dispatcher.get_job = AsyncMock(return_value=mock_job)
    dispatcher.dispatch = AsyncMock()

    service = JobEnqueueService(repo, dispatcher, "default")
    await service.wake_job_row("00000000-0000-4000-8000-000000000001", 5)

    mock_job.remove.assert_not_called()
    dispatcher.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_lease_reclaim_wakes_pending_rows() -> None:
    repo = MagicMock()
    repo.reclaim_expired_leases = AsyncMock(
        return_value=[
            ReclaimedLeaseRow(id="job-1", status="pending", max_attempts=5),
            ReclaimedLeaseRow(id="job-2", status="failed", max_attempts=5),
        ]
    )
    repo.find_orphaned_pending_jobs = AsyncMock(return_value=[])

    enqueue = MagicMock()
    enqueue.wake_job_row = AsyncMock()

    service = JobLeaseReclaimService(
        repo,
        enqueue,
        enabled=True,
        lease_reclaim_enabled=True,
        lease_reclaim_batch_size=25,
    )
    await service.tick()

    repo.reclaim_expired_leases.assert_awaited_once_with(25)
    enqueue.wake_job_row.assert_awaited_once_with("job-1", 5)


@pytest.mark.asyncio
async def test_lease_reclaim_wakes_orphaned_pending_rows() -> None:
    repo = MagicMock()
    repo.reclaim_expired_leases = AsyncMock(return_value=[])
    repo.find_orphaned_pending_jobs = AsyncMock(
        return_value=[{"id": "job-9", "maxAttempts": 5}]
    )

    enqueue = MagicMock()
    enqueue.wake_job_row = AsyncMock()

    service = JobLeaseReclaimService(
        repo,
        enqueue,
        enabled=True,
        lease_reclaim_enabled=True,
        lease_reclaim_batch_size=25,
        orphan_min_age_sec=30.0,
    )
    await service.tick()

    repo.find_orphaned_pending_jobs.assert_awaited_once_with(25, 30.0)
    enqueue.wake_job_row.assert_awaited_once_with("job-9", 5)


@pytest.mark.asyncio
async def test_lease_reclaim_failing_wake_does_not_abort_batch() -> None:
    repo = MagicMock()
    repo.reclaim_expired_leases = AsyncMock(return_value=[])
    repo.find_orphaned_pending_jobs = AsyncMock(
        return_value=[
            {"id": "job-1", "maxAttempts": 3},
            {"id": "job-2", "maxAttempts": 4},
        ]
    )

    enqueue = MagicMock()
    enqueue.wake_job_row = AsyncMock(side_effect=[RuntimeError("Redis blip"), None])

    service = JobLeaseReclaimService(
        repo,
        enqueue,
        enabled=True,
        lease_reclaim_enabled=True,
    )
    await service.tick()

    assert enqueue.wake_job_row.await_count == 2


def test_lease_reclaim_disabled_does_not_start_timer() -> None:
    repo = MagicMock()
    enqueue = MagicMock()
    service = JobLeaseReclaimService(
        repo,
        enqueue,
        enabled=True,
        lease_reclaim_enabled=False,
    )
    service.start()
    assert service.has_active_timer() is False
    service.stop()


@pytest.mark.asyncio
async def test_job_processor_skips_when_claim_none_and_completed() -> None:
    repo = MagicMock()
    repo.claim_by_id = AsyncMock(return_value=None)
    repo.get_status = AsyncMock(return_value="completed")
    registry = JobHandlerRegistry()

    processor = BackgroundJobsRunProcessor(repo, registry)
    await processor.process({"jobRowId": "id-1"})


@pytest.mark.asyncio
async def test_job_processor_skips_when_claim_none_and_processing() -> None:
    repo = MagicMock()
    repo.claim_by_id = AsyncMock(return_value=None)
    repo.get_status = AsyncMock(return_value="processing")
    registry = JobHandlerRegistry()

    processor = BackgroundJobsRunProcessor(repo, registry)
    await processor.process({"jobRowId": "id-1"})


@pytest.mark.asyncio
async def test_job_processor_raises_when_claim_none_and_unclaimable() -> None:
    repo = MagicMock()
    repo.claim_by_id = AsyncMock(return_value=None)
    repo.get_status = AsyncMock(return_value="failed")
    registry = JobHandlerRegistry()

    processor = BackgroundJobsRunProcessor(repo, registry)
    with pytest.raises(RuntimeError, match="Unclaimable background job"):
        await processor.process({"jobRowId": "id-1"})


@pytest.mark.asyncio
async def test_job_processor_runs_handler_and_completes() -> None:
    repo = MagicMock()
    row = BackgroundJobRow(
        id="id-1",
        queue_name="default",
        job_name="example.echo",
        payload={"message": "x"},
        attempts=1,
        max_attempts=5,
    )
    repo.claim_by_id = AsyncMock(return_value=row)
    repo.mark_completed = AsyncMock()

    handler = ExampleEchoJobHandler()
    registry = JobHandlerRegistry()
    registry.register("example.echo", handler)

    processor = BackgroundJobsRunProcessor(repo, registry)
    await processor.process({"jobRowId": "id-1"})

    repo.mark_completed.assert_awaited_once_with("id-1")


@pytest.mark.asyncio
async def test_job_processor_retries_when_handler_fails_below_max() -> None:
    repo = MagicMock()
    row = BackgroundJobRow(
        id="id-1",
        queue_name="default",
        job_name="fail_job",
        payload={},
        attempts=2,
        max_attempts=5,
    )
    repo.claim_by_id = AsyncMock(return_value=row)
    repo.mark_failed = AsyncMock()

    mock_handler = MagicMock()
    mock_handler.handle = AsyncMock(side_effect=RuntimeError("temporary error"))

    registry = JobHandlerRegistry()
    registry.register("fail_job", mock_handler)

    processor = BackgroundJobsRunProcessor(repo, registry)
    await processor.process({"jobRowId": "id-1"})

    repo.mark_failed.assert_awaited_once()
    args, _ = repo.mark_failed.await_args
    assert args[0] == "id-1"
    # requeue_at is provided when attempts < max_attempts
    assert args[2] is not None


@pytest.mark.asyncio
async def test_job_processor_marks_failed_when_max_attempts_reached() -> None:
    repo = MagicMock()
    row = BackgroundJobRow(
        id="id-1",
        queue_name="default",
        job_name="fail_job",
        payload={},
        attempts=5,
        max_attempts=5,
    )
    repo.claim_by_id = AsyncMock(return_value=row)
    repo.mark_failed = AsyncMock()

    mock_handler = MagicMock()
    mock_handler.handle = AsyncMock(side_effect=RuntimeError("fatal error"))

    registry = JobHandlerRegistry()
    registry.register("fail_job", mock_handler)

    processor = BackgroundJobsRunProcessor(repo, registry)
    await processor.process({"jobRowId": "id-1"})

    repo.mark_failed.assert_awaited_once()
    args, _ = repo.mark_failed.await_args
    assert args[0] == "id-1"
    # No requeue_at when max attempts reached
    assert len(args) == 2 or args[2] is None


@pytest.mark.asyncio
async def test_example_jobs_controller_enqueues_echo() -> None:
    job_enqueue = MagicMock()
    job_enqueue.enqueue = AsyncMock(
        return_value=MagicMock(job_row_id="id-1", enqueued=True, deduplicated=False)
    )

    dto = EnqueueEchoJobDto(message="hello", dedupeKey=None)
    res = await enqueue_echo(dto, job_enqueue)

    assert res["jobRowId"] == "id-1"
    assert res["enqueued"] is True
    assert res["deduplicated"] is False
