"""Opaque-box background job Postgres ledger and Celery worker helpers."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.e2e.helpers.containers import resolve_project_root
from tests.e2e.helpers.server import resolve_python_executable

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True, slots=True)
class BackgroundJobRow:
    """Snapshot of a row in PostgreSQL ``background_jobs``."""

    id: str
    queue_name: str
    job_name: str
    dedupe_key: str | None
    payload: dict[str, Any]
    status: str
    run_at: datetime
    attempts: int
    max_attempts: int
    locked_by: str | None
    locked_at: datetime | None
    lease_until: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


def _resolve_dsn(target: str | Any) -> str:
    if isinstance(target, str):
        return target
    if hasattr(target, "postgres_dsn"):
        return str(target.postgres_dsn)
    if hasattr(target, "dsn"):
        return str(target.dsn)
    pg = getattr(target, "postgres", None)
    if pg is not None and hasattr(pg, "dsn"):
        return str(pg.dsn)
    msg = f"Cannot resolve PostgreSQL DSN from {target!r}"
    raise TypeError(msg)


def _row_to_model(row: Any) -> BackgroundJobRow:
    raw_payload = row["payload"]
    if isinstance(raw_payload, str):
        parsed_payload: dict[str, Any] = json.loads(raw_payload)
    elif isinstance(raw_payload, Mapping):
        parsed_payload = dict(raw_payload)
    else:
        parsed_payload = {}
    return BackgroundJobRow(
        id=str(row["id"]),
        queue_name=str(row["queue_name"]),
        job_name=str(row["job_name"]),
        dedupe_key=row["dedupe_key"],
        payload=parsed_payload,
        status=str(row["status"]),
        run_at=row["run_at"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        locked_by=row["locked_by"],
        locked_at=row["locked_at"],
        lease_until=row["lease_until"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


_SELECT_COLUMNS = (
    "id::text, queue_name, job_name, dedupe_key, payload::text, status::text, "
    "run_at, attempts, max_attempts, locked_by, locked_at, lease_until, "
    "last_error, created_at, updated_at, completed_at"
)


async def fetch_job(target: str | Any, job_id: str) -> BackgroundJobRow | None:
    """Fetch a single row from ``background_jobs`` by UUID."""
    import asyncpg

    conn = await asyncpg.connect(_resolve_dsn(target))
    try:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM background_jobs WHERE id = $1::uuid",  # noqa: S608
            job_id,
        )
        return _row_to_model(row) if row is not None else None
    finally:
        await conn.close()


async def fetch_jobs_by_dedupe_key(
    target: str | Any,
    dedupe_key: str,
) -> list[BackgroundJobRow]:
    """Fetch all ``background_jobs`` rows matching ``dedupe_key``."""
    import asyncpg

    conn = await asyncpg.connect(_resolve_dsn(target))
    try:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM background_jobs "  # noqa: S608
            "WHERE dedupe_key = $1 ORDER BY created_at ASC",
            dedupe_key,
        )
        return [_row_to_model(r) for r in rows]
    finally:
        await conn.close()


async def wait_for_job_status(
    target: str | Any,
    job_id: str,
    expected_statuses: str | Iterable[str],
    *,
    timeout_sec: float = 15.0,
    poll_interval_sec: float = 0.1,
) -> BackgroundJobRow:
    """Poll ``background_jobs`` until ``job_id`` enters one of ``expected_statuses``."""
    expected = (
        {expected_statuses}
        if isinstance(expected_statuses, str)
        else set(expected_statuses)
    )
    deadline = time.monotonic() + timeout_sec
    last_row: BackgroundJobRow | None = None
    while time.monotonic() < deadline:
        last_row = await fetch_job(target, job_id)
        if last_row is not None and last_row.status in expected:
            return last_row
        await asyncio.sleep(poll_interval_sec)
    msg = (
        f"Timed out after {timeout_sec}s waiting for job {job_id} "
        f"to reach {sorted(expected)}; last_row={last_row!r}"
    )
    raise TimeoutError(msg)


async def insert_job_row(
    target: str | Any,
    *,
    queue_name: str = "default",
    job_name: str = "example.echo",
    payload: Mapping[str, Any] | None = None,
    dedupe_key: str | None = None,
    status: str = "pending",
    attempts: int = 0,
    max_attempts: int = 5,
    run_at_offset_sec: float = 0.0,
    lease_offset_sec: float | None = None,
    locked_by: str | None = None,
) -> str:
    """Insert a synthetic row into ``background_jobs`` and return its UUID."""
    import asyncpg

    payload_json = json.dumps(dict(payload) if payload is not None else {})
    conn = await asyncpg.connect(_resolve_dsn(target))
    try:
        row = await conn.fetchrow(
            "INSERT INTO background_jobs ("
            "  queue_name, job_name, dedupe_key, payload, status, "
            "  run_at, attempts, max_attempts, locked_by, locked_at, lease_until"
            ") VALUES ("
            "  $1, $2, $3, $4::jsonb, $5::background_job_status, "
            "  NOW() + make_interval(secs => $6), $7, $8, $9, "
            "  CASE WHEN $9 IS NOT NULL THEN NOW() ELSE NULL END, "
            "  CASE WHEN $10::double precision IS NOT NULL "
            "       THEN NOW() + make_interval(secs => $10) ELSE NULL END"
            ") RETURNING id::text",
            queue_name,
            job_name,
            dedupe_key,
            payload_json,
            status,
            float(run_at_offset_sec),
            attempts,
            max_attempts,
            locked_by,
            float(lease_offset_sec) if lease_offset_sec is not None else None,
        )
        assert row is not None
        return str(row["id"])
    finally:
        await conn.close()


async def expire_job_lease(
    target: str | Any,
    job_id: str,
    *,
    seconds_ago: float = 10.0,
    locked_by: str = "crashed-worker:0",
) -> None:
    """Force a job into an expired ``processing`` lease state for reclaim testing."""
    import asyncpg

    conn = await asyncpg.connect(_resolve_dsn(target))
    try:
        await conn.execute(
            "UPDATE background_jobs "
            "SET status = 'processing'::background_job_status, "
            "    locked_by = COALESCE(locked_by, $2), "
            "    locked_at = COALESCE(locked_at, NOW() - make_interval(secs => $3)), "
            "    lease_until = NOW() - make_interval(secs => $3), "
            "    updated_at = NOW() "
            "WHERE id = $1::uuid",
            job_id,
            locked_by,
            float(seconds_ago),
        )
    finally:
        await conn.close()


async def backdate_pending_job(
    target: str | Any,
    job_id: str,
    *,
    seconds_ago: float = 60.0,
) -> None:
    """Backdate ``run_at`` on a ``pending`` job to simulate an orphaned queued row."""
    import asyncpg

    conn = await asyncpg.connect(_resolve_dsn(target))
    try:
        await conn.execute(
            "UPDATE background_jobs "
            "SET run_at = NOW() - make_interval(secs => $2), "
            "    updated_at = NOW() "
            "WHERE id = $1::uuid AND status = 'pending'::background_job_status",
            job_id,
            float(seconds_ago),
        )
    finally:
        await conn.close()


def fetch_job_sync(target: str | Any, job_id: str) -> BackgroundJobRow | None:
    """Synchronous wrapper for :func:`fetch_job`."""
    return asyncio.run(fetch_job(target, job_id))


def wait_for_job_status_sync(
    target: str | Any,
    job_id: str,
    expected_statuses: str | Iterable[str],
    *,
    timeout_sec: float = 15.0,
    poll_interval_sec: float = 0.1,
) -> BackgroundJobRow:
    """Synchronous wrapper for :func:`wait_for_job_status`."""
    return asyncio.run(
        wait_for_job_status(
            target,
            job_id,
            expected_statuses,
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )
    )


def expire_job_lease_sync(
    target: str | Any,
    job_id: str,
    *,
    seconds_ago: float = 10.0,
    locked_by: str = "crashed-worker:0",
) -> None:
    """Synchronous wrapper for :func:`expire_job_lease`."""
    asyncio.run(
        expire_job_lease(
            target,
            job_id,
            seconds_ago=seconds_ago,
            locked_by=locked_by,
        )
    )


async def enqueue_echo_job(
    client: httpx.AsyncClient,
    message: str,
    *,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """Call ``POST /example/jobs/echo`` and return parsed JSON response."""
    body: dict[str, Any] = {"message": message}
    if dedupe_key is not None:
        body["dedupeKey"] = dedupe_key
    resp = await client.post("/example/jobs/echo", json=body)
    assert resp.status_code == 201, (
        f"Expected 201 from POST /example/jobs/echo, "
        f"got {resp.status_code}: {resp.text}"
    )
    data: dict[str, Any] = resp.json()
    return data


class CeleryWorkerProcess:
    """Manages an isolated Celery worker subprocess for E2E background job tests."""

    def __init__(
        self,
        env: Mapping[str, str | None],
        *,
        app_path: str = "app.core.background_jobs.celery_app",
        queues: str = "background-jobs,celery,default",
        pool: str = "solo",
        concurrency: int = 1,
        loglevel: str = "INFO",
        cwd: str | Path | None = None,
    ) -> None:
        self._env = dict(env)
        self._app_path = app_path
        self._queues = queues
        self._pool = pool
        self._concurrency = concurrency
        self._loglevel = loglevel
        self._cwd = Path(cwd) if cwd is not None else resolve_project_root()
        self._proc: asyncio.subprocess.Process | None = None
        self._output_lines: list[str] = []
        self._drain_tasks: list[asyncio.Task[None]] = []

    @property
    def output_lines(self) -> list[str]:
        """Return captured stdout and stderr lines from the worker."""
        return list(self._output_lines)

    def _build_env(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER"):
            val = os.environ.get(key)
            if val is not None:
                out[key] = val
        out["PYTHONPATH"] = str(self._cwd)
        out["PYTHONUNBUFFERED"] = "1"
        for key, val in self._env.items():
            if val is None:
                out.pop(key, None)
            else:
                out[key] = str(val)
        return out

    async def _drain_stream(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                break
            self._output_lines.append(
                raw.decode("utf-8", errors="replace").rstrip("\r\n")
            )

    async def start(
        self,
        *,
        startup_wait_sec: float = 2.0,
        ready_timeout_sec: float = 15.0,
    ) -> None:
        """Launch the Celery worker and wait until ready or ``startup_wait_sec``."""
        if self._proc is not None:
            return
        exe = resolve_python_executable(self._cwd)
        cmd = [
            exe,
            "-m",
            "celery",
            "-A",
            self._app_path,
            "worker",
            f"--pool={self._pool}",
            f"--concurrency={self._concurrency}",
            f"--loglevel={self._loglevel}",
            "-Q",
            self._queues,
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self._cwd),
            env=self._build_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._drain_tasks = [
            asyncio.create_task(self._drain_stream(self._proc.stdout)),
        ]
        deadline = time.monotonic() + ready_timeout_sec
        min_ready_at = time.monotonic() + startup_wait_sec
        try:
            while time.monotonic() < deadline:
                if self._proc.returncode is not None:
                    await asyncio.gather(*self._drain_tasks, return_exceptions=True)
                    logs = "\n".join(self._output_lines)
                    msg = (
                        f"Celery worker exited prematurely ({self._proc.returncode}).\n"
                        f"Logs:\n{logs}"
                    )
                    raise RuntimeError(msg)
                if any("ready." in line.lower() for line in self._output_lines):
                    return
                if time.monotonic() >= min_ready_at and self._output_lines:
                    return
                await asyncio.sleep(0.1)

            logs = "\n".join(self._output_lines)
            msg = (
                f"Timed out after {ready_timeout_sec}s waiting for Celery worker "
                f"readiness.\nLogs:\n{logs}"
            )
            raise TimeoutError(msg)
        except BaseException:
            await self.stop()
            raise

    async def stop(self, *, timeout_sec: float = 10.0) -> int:
        """Terminate the Celery worker gracefully (SIGTERM -> SIGKILL)."""
        if self._proc is None:
            return 0
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=timeout_sec)
            except TimeoutError:
                self._proc.kill()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        for task in self._drain_tasks:
            task.cancel()
        if self._drain_tasks:
            await asyncio.gather(*self._drain_tasks, return_exceptions=True)
        self._drain_tasks.clear()
        return int(self._proc.returncode or 0)

    async def __aenter__(self) -> CeleryWorkerProcess:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


__all__ = [
    "BackgroundJobRow",
    "CeleryWorkerProcess",
    "backdate_pending_job",
    "enqueue_echo_job",
    "expire_job_lease",
    "expire_job_lease_sync",
    "fetch_job",
    "fetch_job_sync",
    "fetch_jobs_by_dedupe_key",
    "insert_job_row",
    "wait_for_job_status",
    "wait_for_job_status_sync",
]
