from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.background_jobs.constants import BACKGROUND_JOBS_DEFAULT_QUEUE

if TYPE_CHECKING:
    from celery import Celery


class CeleryJobProxy:
    """Proxy providing state and removal methods for Celery tasks."""

    def __init__(self, task_id: str, celery_app: Celery) -> None:
        self.id = task_id
        self._celery_app = celery_app

    def get_state(self) -> str:
        async_result = self._celery_app.AsyncResult(self.id)
        state = str(async_result.state).lower()
        if state in ("pending", "received"):
            return "waiting"
        if state == "started":
            return "active"
        if state == "success":
            return "completed"
        return state

    def remove(self) -> None:
        self._celery_app.control.revoke(self.id, terminate=True)


class CeleryQueueDispatcher:
    """Dispatches wake tasks to Celery Redis broker matching dispatcher interface."""

    def __init__(
        self,
        celery_app: Celery,
        default_queue: str = BACKGROUND_JOBS_DEFAULT_QUEUE,
    ) -> None:
        self._celery_app = celery_app
        self._default_queue = default_queue

    def dispatch(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        queue: str | None = None,
        countdown: float | None = None,
    ) -> Any:
        target_queue = queue or self._default_queue
        task_id = payload.get("jobRowId")
        return self._celery_app.send_task(
            name,
            args=[payload],
            task_id=task_id,
            queue=target_queue,
            countdown=countdown,
        )

    def add(self, name: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        return self.dispatch(name, payload, **kwargs)

    def get_job(self, job_id: str) -> CeleryJobProxy:
        return CeleryJobProxy(job_id, self._celery_app)


__all__ = [
    "CeleryJobProxy",
    "CeleryQueueDispatcher",
]
