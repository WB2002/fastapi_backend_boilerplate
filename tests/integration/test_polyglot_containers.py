"""Live container integration tests for PostgreSQL, Redis, and MongoDB."""

from __future__ import annotations

import datetime
import os
import shutil
from typing import Any

import pytest

from app.auth.schemas.user_schema import User, UserRepository
from app.common.mongo.mongo_service import MongoService
from app.common.postgres.postgres_pool import PostgresPool, PostgresPoolOptions
from app.common.redis.redis_service import RedisService
from app.core.background_jobs.repository import PostgresJobRepository
from app.core.background_jobs.types import ClaimJobInput, InsertPendingJobInput
from tests.e2e.helpers.containers import BackingServices, apply_postgres_migrations


def is_docker_available() -> bool:
    """Return True if Docker is installed and the daemon appears reachable."""
    if not shutil.which("docker"):
        return False
    return os.path.exists("/var/run/docker.sock") or bool(os.environ.get("DOCKER_HOST"))


pytestmark = pytest.mark.skipif(
    not is_docker_available(),
    reason="Docker daemon unavailable for testcontainers",
)


@pytest.mark.asyncio
async def test_postgres_migrations_and_jobs_repository() -> None:
    """Validate Postgres migrations 001 and 002 and background job persistence."""
    services = BackingServices.start(
        enable_postgres=True,
        enable_mongo=False,
        enable_redis=False,
    )
    try:
        applied = await apply_postgres_migrations(services)
        assert len(applied) >= 2

        opts = PostgresPoolOptions(
            host=services.postgres_host,
            port=services.postgres_port,
            user=services.postgres_user,
            password=services.postgres_password,
            database=services.postgres_db,
            min_size=1,
            max_size=5,
        )
        pool = PostgresPool(options=opts)
        try:
            repo = PostgresJobRepository(pool)
            job_id = await repo.insert_pending(
                InsertPendingJobInput(
                    queue_name="default",
                    job_name="integration.echo",
                    payload={"msg": "hello from testcontainers"},
                    run_at=datetime.datetime.now(datetime.UTC),
                    max_attempts=3,
                    dedupe_key="dedupe-tc-1",
                )
            )
            assert job_id is not None

            claimed = await repo.claim_by_id(
                ClaimJobInput(
                    id=job_id,
                    worker_id="tc-worker-1",
                    lease_sec=30.0,
                )
            )
            assert claimed is not None
            assert claimed.id == job_id
        finally:
            await pool.close()
    finally:
        services.stop()


@pytest.mark.asyncio
async def test_redis_atomic_operations() -> None:
    """Validate live Redis connection and atomic Lua script execution."""
    services = BackingServices.start(
        enable_postgres=False,
        enable_mongo=False,
        enable_redis=True,
    )
    try:
        redis_service = RedisService(
            host=services.redis_host,
            port=services.redis_port,
        )
        try:
            client = redis_service.get_client()
            assert client is not None
            await client.set("tc:key", "value", ex=60)
            res = await redis_service.get("tc:key")
            assert res == "value"

            # Test Lua release script
            script = client.register_script(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end"
            )
            deleted: Any = await script(keys=["tc:key"], args=["value"])
            assert deleted == 1
            assert await redis_service.get("tc:key") is None
        finally:
            await redis_service.close()
    finally:
        services.stop()


@pytest.mark.asyncio
async def test_mongo_document_persistence() -> None:
    """Validate MongoDB connection and dual-mode ODM persistence."""
    services = BackingServices.start(
        enable_postgres=False,
        enable_mongo=True,
        enable_redis=False,
    )
    try:
        mongo_service = MongoService(mongo_uri=services.mongo_uri)
        try:
            user_repo = UserRepository(mongo_service=mongo_service)
            user = User(
                email="tc_user@example.com",
                passwordHash="argon2id$mockedhash",
            )
            saved = await user_repo.create(user)
            assert saved.id is not None

            found = await user_repo.find_by_email("tc_user@example.com")
            assert found is not None
            assert found.id == saved.id
        finally:
            await mongo_service.close()
    finally:
        services.stop()
