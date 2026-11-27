from __future__ import annotations

import asyncio
import inspect
import os
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

import psutil
from fastapi import APIRouter, HTTPException, Request

from app.common.config.parsers import parse_js_int, parse_positive_int
from app.common.decorators import (
    public,
    skip_country_access,
    skip_inbound_bulkhead,
    skip_rate_limit,
    skip_request_deadline,
    skip_tracing,
)
from app.common.mongo.mongo_service import MongoService
from app.common.postgres.postgres_pool import PostgresPool, PostgresPoolOptions
from app.common.redis.redis_service import RedisService
from app.core.health.health_snapshot import HealthSnapshot
from app.core.shutdown.shutdown_state import ShutdownState, get_shutdown_state

DEFAULT_HEALTH_CACHE_TTL_MS: Final[int] = 2_000
DEFAULT_HEAP_THRESHOLD_BYTES: Final[int] = 150 * 1024 * 1024
DEFAULT_STORAGE_THRESHOLD_PERCENT: Final[float] = 0.9
REDIS_PING_TIMEOUT_MS: Final[int] = 3_000
POSTGRES_CONNECT_TIMEOUT_MS: Final[int] = 2_000

type HealthIndicatorResult = dict[str, dict[str, Any]]
type HealthIndicatorFunction = Callable[[], Awaitable[HealthIndicatorResult]]


class ServiceUnavailableException(HTTPException):
    """Terminus/NestJS-compatible 503 exception."""

    def __init__(self, response: str | dict[str, Any]) -> None:
        super().__init__(status_code=503, detail=response)
        self.response = response

    def get_status(self) -> int:
        return 503

    def getStatus(self) -> int:
        return 503

    def get_response(self) -> str | dict[str, Any]:
        return self.response

    def getResponse(self) -> str | dict[str, Any]:
        return self.response


class HealthCheckService:
    """Terminus-compatible HealthCheckService aggregating indicator results."""

    async def check(
        self,
        indicators: Sequence[HealthIndicatorFunction],
    ) -> dict[str, Any]:
        info: dict[str, Any] = {}
        error: dict[str, Any] = {}
        details: dict[str, Any] = {}

        results = await asyncio.gather(*(ind() for ind in indicators))
        for res in results:
            for key, val in res.items():
                details[key] = val
                if val.get("status") == "up":
                    info[key] = val
                else:
                    error[key] = val

        if error:
            payload: dict[str, Any] = {
                "status": "error",
                "info": info,
                "error": error,
                "details": details,
            }
            raise ServiceUnavailableException(payload)

        return {
            "status": "ok",
            "info": info,
            "error": error,
            "details": details,
        }


class MongooseHealthIndicator:
    """Terminus-compatible MongoDB ping health indicator."""

    def __init__(self, mongo_service: MongoService | None = None) -> None:
        self._mongo_service = mongo_service

    async def ping_check(self, key: str = "mongo") -> HealthIndicatorResult:
        if self._mongo_service is None:
            raise RuntimeError("MongoDB service is not configured")
        ok = await self._mongo_service.ping()
        if not ok:
            raise RuntimeError("MongoDB ping failed")
        return {key: {"status": "up"}}

    async def pingCheck(self, key: str = "mongo") -> HealthIndicatorResult:
        return await self.ping_check(key)


class MemoryHealthIndicator:
    """Terminus-compatible process memory heap indicator."""

    async def check_heap(
        self,
        key: str = "memory_heap",
        threshold_bytes: int = DEFAULT_HEAP_THRESHOLD_BYTES,
    ) -> HealthIndicatorResult:
        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            # Python process USS/RSS check; subtract shared libraries where possible
            used_bytes = int(getattr(mem_info, "rss", 0))
            # Check against threshold; in Python, RSS includes C libraries (~60-90MB),
            # so use children/USS or check against threshold
            if used_bytes <= threshold_bytes:
                return {key: {"status": "up"}}
            return {
                key: {
                    "status": "down",
                    "message": (
                        f"Exceeded heap threshold of {threshold_bytes} bytes "
                        f"(current: {used_bytes})"
                    ),
                }
            }
        except Exception:
            return {key: {"status": "up"}}

    async def checkHeap(
        self,
        key: str = "memory_heap",
        threshold_bytes: int = DEFAULT_HEAP_THRESHOLD_BYTES,
    ) -> HealthIndicatorResult:
        return await self.check_heap(key, threshold_bytes)


class DiskHealthIndicator:
    """Terminus-compatible disk storage usage indicator."""

    async def check_storage(
        self,
        key: str = "storage",
        options: Mapping[str, Any] | None = None,
    ) -> HealthIndicatorResult:
        opts = options or {}
        path = str(opts.get("path") or "/")
        threshold_percent = float(
            opts.get("thresholdPercent")
            or opts.get("threshold_percent")
            or DEFAULT_STORAGE_THRESHOLD_PERCENT
        )
        try:
            usage = shutil.disk_usage(path)
            used_ratio = usage.used / usage.total if usage.total > 0 else 0.0
            if used_ratio <= threshold_percent:
                return {key: {"status": "up"}}
            return {
                key: {
                    "status": "down",
                    "message": (
                        f"Disk storage usage {used_ratio:.2%} exceeds "
                        f"threshold {threshold_percent:.2%}"
                    ),
                }
            }
        except Exception as exc:
            return {key: {"status": "down", "message": str(exc)}}

    async def checkStorage(
        self,
        key: str = "storage",
        options: Mapping[str, Any] | None = None,
    ) -> HealthIndicatorResult:
        return await self.check_storage(key, options)


class MicroserviceHealthIndicator:
    """Terminus-compatible Redis TCP/PING health indicator."""

    def __init__(self, redis_service: RedisService | None = None) -> None:
        self._redis_service = redis_service

    async def ping_check(
        self,
        key: str = "redis",
        options: Mapping[str, Any] | None = None,
        *,
        timeout_ms: int = REDIS_PING_TIMEOUT_MS,
    ) -> HealthIndicatorResult:
        timeout_sec = max(0.001, timeout_ms / 1000.0)
        try:
            if self._redis_service is not None:
                ok = await asyncio.wait_for(
                    self._redis_service.ping(),
                    timeout=timeout_sec,
                )
                if ok:
                    return {key: {"status": "up"}}
                return {key: {"status": "down", "message": "Redis ping returned False"}}

            opts = (options or {}).get("options") or options or {}
            host = str(opts.get("host") or "localhost")
            port = int(opts.get("port") or 6379)
            temp_redis = RedisService(
                host=host,
                port=port,
                connect_timeout_ms=timeout_ms,
            )
            try:
                ok = await asyncio.wait_for(temp_redis.ping(), timeout=timeout_sec)
                if ok:
                    return {key: {"status": "up"}}
                return {key: {"status": "down", "message": "Redis ping failed"}}
            finally:
                await temp_redis.on_module_destroy()
        except Exception as exc:
            msg = str(exc) or "Redis unavailable"
            return {key: {"status": "down", "message": msg}}

    async def pingCheck(
        self,
        key: str = "redis",
        options: Mapping[str, Any] | None = None,
        *,
        timeout_ms: int = REDIS_PING_TIMEOUT_MS,
    ) -> HealthIndicatorResult:
        return await self.ping_check(key, options, timeout_ms=timeout_ms)


class FeatureFlagHealthIndicator:
    """Reports feature-flag subsystem status (informational; never fails readiness)."""

    def __init__(self, feature_flag_service: Any = None) -> None:
        self._feature_flag_service = feature_flag_service

    async def is_healthy(
        self,
        key: str = "feature_flags",
    ) -> HealthIndicatorResult:
        if self._feature_flag_service is not None:
            get_status = getattr(
                self._feature_flag_service,
                "get_health_status",
                None,
            ) or getattr(
                self._feature_flag_service,
                "getHealthStatus",
                None,
            )
            if callable(get_status):
                status = get_status()
                if inspect.isawaitable(status):
                    status = await status
                if isinstance(status, Mapping):
                    return {
                        key: {
                            "status": "up",
                            "stale": bool(status.get("stale", False)),
                            "healthy": bool(status.get("healthy", True)),
                            "redisAvailable": bool(status.get("redisAvailable", True)),
                            "pubSubConnected": bool(
                                status.get("pubSubConnected", False)
                            ),
                            "flagCount": int(status.get("flagCount", 0)),
                            "lastReconciledAt": status.get("lastReconciledAt"),
                            "lastError": status.get("lastError"),
                        }
                    }

        return {
            key: {
                "status": "up",
                "stale": False,
                "healthy": True,
                "redisAvailable": True,
                "pubSubConnected": False,
                "flagCount": 0,
                "lastReconciledAt": None,
                "lastError": None,
            }
        }

    async def isHealthy(
        self,
        key: str = "feature_flags",
    ) -> HealthIndicatorResult:
        return await self.is_healthy(key)


def _resolve_health_ttl_ms(
    config: Mapping[str, Any] | Any,
) -> int:
    raw_ttl: Any = None
    if isinstance(config, Mapping):
        raw_ttl = config.get("HEALTH_CHECK_CACHE_TTL_MS")
    elif config is not None and hasattr(config, "get"):
        raw_ttl = config.get("HEALTH_CHECK_CACHE_TTL_MS")
    if isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool):
        return raw_ttl if raw_ttl >= 0 else DEFAULT_HEALTH_CACHE_TTL_MS
    parsed = parse_js_int(str(raw_ttl) if raw_ttl is not None else None)
    return parsed if parsed is not None and parsed >= 0 else DEFAULT_HEALTH_CACHE_TTL_MS


def _config_get(
    config: Mapping[str, Any] | Any,
    key: str,
    default: Any = None,
) -> Any:
    if isinstance(config, Mapping):
        val = config.get(key)
        return default if val is None else val
    if config is not None and hasattr(config, "get"):
        try:
            val = config.get(key)
            return default if val is None else val
        except TypeError:
            val = config.get(key, default)
            return default if val is None else val
    return default


@public
@skip_rate_limit
@skip_request_deadline
@skip_country_access
@skip_inbound_bulkhead
@skip_tracing
class HealthController:
    """Terminus HealthController (`/health`, `/health/ready`, `/health/live`)."""

    def __init__(
        self,
        health: HealthCheckService | None = None,
        mongoose: MongooseHealthIndicator | Any | None = None,
        memory: MemoryHealthIndicator | Any | None = None,
        disk: DiskHealthIndicator | Any | None = None,
        microservice: MicroserviceHealthIndicator | Any | None = None,
        config_service: Mapping[str, Any] | Any | None = None,
        shutdown_state: ShutdownState | None = None,
        feature_flag_health: FeatureFlagHealthIndicator | Any | None = None,
        *,
        mongo_service: MongoService | None = None,
        redis_service: RedisService | None = None,
        pg_pool: PostgresPool | Any | None = None,
    ) -> None:
        self.health = health or HealthCheckService()
        self.mongoose = mongoose or MongooseHealthIndicator(mongo_service)
        self.memory = memory or MemoryHealthIndicator()
        self.disk = disk or DiskHealthIndicator()
        self.microservice = microservice or MicroserviceHealthIndicator(redis_service)
        self.config_service = (
            config_service if config_service is not None else os.environ
        )
        self.shutdown_state = shutdown_state or get_shutdown_state()
        self.feature_flag_health = feature_flag_health or FeatureFlagHealthIndicator()
        self.pg_pool: PostgresPool | Any | None = pg_pool

        ttl_ms = _resolve_health_ttl_ms(self.config_service)
        self.snapshot: HealthSnapshot[dict[str, Any]] = HealthSnapshot(ttl_ms)

    @property
    def pgPool(self) -> Any:
        return self.pg_pool

    @pgPool.setter
    def pgPool(self, value: Any) -> None:
        self.pg_pool = value

    def live(self) -> dict[str, str]:
        """Liveness probe (`GET /health/live`). Returns 503 if shutting down."""
        if self.shutdown_state.is_shutting_down:
            raise ServiceUnavailableException("Application is shutting down")
        return {"status": "ok"}

    async def check(self) -> dict[str, Any]:
        """Aggregate health probe (`GET /health`). Does NOT check shutdown state."""
        return await self.snapshot.get(lambda: self.health.check(self.build_checks()))

    async def ready(self) -> dict[str, Any]:
        """Readiness probe (`GET /health/ready`). Fails immediately on shutdown."""
        if self.shutdown_state.is_shutting_down:

            async def _shutting_down_indicator() -> HealthIndicatorResult:
                return {
                    "shuttingDown": {
                        "status": "down",
                        "message": "Application is shutting down",
                    }
                }

            return await self.health.check([_shutting_down_indicator])
        return await self.snapshot.get(lambda: self.health.check(self.build_checks()))

    async def on_module_destroy(self) -> None:
        """Close the lazy Postgres health check pool if initialized."""
        pool = self.pg_pool
        self.pg_pool = None
        if pool is None:
            return
        try:
            end_fn = getattr(pool, "end", None) or getattr(pool, "close", None)
            if callable(end_fn):
                res = end_fn()
                if inspect.isawaitable(res):
                    await res
        except Exception:  # noqa: S110
            pass

    async def onModuleDestroy(self) -> None:
        await self.on_module_destroy()

    async def _check_mongo_indicator(self) -> HealthIndicatorResult:
        try:
            ping_fn = getattr(self.mongoose, "ping_check", None) or getattr(
                self.mongoose, "pingCheck", None
            )
            if callable(ping_fn):
                res = ping_fn("mongo")
                if inspect.isawaitable(res):
                    res = await res
                return dict(res)
            return {"mongo": {"status": "down", "message": "Unavailable"}}
        except Exception:
            return {"mongo": {"status": "down", "message": "Unavailable"}}

    async def _check_memory_indicator(self) -> HealthIndicatorResult:
        check_fn = getattr(self.memory, "check_heap", None) or getattr(
            self.memory, "checkHeap", None
        )
        if not callable(check_fn):
            return {"memory_heap": {"status": "up"}}
        res = check_fn("memory_heap", DEFAULT_HEAP_THRESHOLD_BYTES)
        if inspect.isawaitable(res):
            res = await res
        return dict(res)

    async def _check_disk_indicator(self) -> HealthIndicatorResult:
        check_fn = getattr(self.disk, "check_storage", None) or getattr(
            self.disk, "checkStorage", None
        )
        if not callable(check_fn):
            return {"storage": {"status": "up"}}
        res = check_fn(
            "storage",
            {
                "path": "/",
                "thresholdPercent": DEFAULT_STORAGE_THRESHOLD_PERCENT,
            },
        )
        if inspect.isawaitable(res):
            res = await res
        return dict(res)

    async def _check_feature_flags_indicator(self) -> HealthIndicatorResult:
        check_fn = getattr(self.feature_flag_health, "is_healthy", None) or getattr(
            self.feature_flag_health, "isHealthy", None
        )
        if not callable(check_fn):
            return {"feature_flags": {"status": "up"}}
        res = check_fn("feature_flags")
        if inspect.isawaitable(res):
            res = await res
        return dict(res)

    async def _check_redis_indicator(
        self,
        redis_host: str,
        redis_port: int,
    ) -> HealthIndicatorResult:
        ping_fn = getattr(self.microservice, "ping_check", None) or getattr(
            self.microservice, "pingCheck", None
        )
        if not callable(ping_fn):
            return {"redis": {"status": "down", "message": "Not configured"}}
        try:
            res = ping_fn(
                "redis",
                {
                    "transport": "REDIS",
                    "options": {
                        "host": redis_host,
                        "port": redis_port,
                    },
                },
            )
            with_timeout = getattr(res, "withTimeout", None)
            if callable(with_timeout):
                res = with_timeout(REDIS_PING_TIMEOUT_MS)
            if inspect.isawaitable(res):
                res = await asyncio.wait_for(
                    res,
                    timeout=REDIS_PING_TIMEOUT_MS / 1000.0,
                )
            return dict(res)
        except Exception as exc:
            return {
                "redis": {
                    "status": "down",
                    "message": str(exc) or "Redis check failed",
                }
            }

    def build_checks(self) -> list[HealthIndicatorFunction]:
        checks: list[HealthIndicatorFunction] = [
            self._check_mongo_indicator,
            self._check_memory_indicator,
            self._check_disk_indicator,
            self._check_feature_flags_indicator,
        ]

        redis_host = _config_get(self.config_service, "REDIS_HOST")
        if isinstance(redis_host, str) and redis_host.strip():
            raw_port = _config_get(self.config_service, "REDIS_PORT", 6379)
            redis_port = (
                raw_port
                if isinstance(raw_port, int) and raw_port > 0
                else parse_positive_int(str(raw_port), 6379)
            )
            host_clean = redis_host.strip()
            checks.append(lambda: self._check_redis_indicator(host_clean, redis_port))

        postgres_db = _config_get(self.config_service, "POSTGRES_DB")
        if isinstance(postgres_db, str) and postgres_db.strip():
            checks.append(self.ping_postgres)

        return checks

    def buildChecks(self) -> list[HealthIndicatorFunction]:
        return self.build_checks()

    def _get_pg_pool(self) -> PostgresPool | Any:
        if self.pg_pool is None:
            raw_host = _config_get(self.config_service, "POSTGRES_HOST", "localhost")
            host = (
                str(raw_host).strip()
                if raw_host is not None and str(raw_host).strip()
                else "localhost"
            )
            raw_port = _config_get(self.config_service, "POSTGRES_PORT", 5432)
            port = (
                raw_port
                if isinstance(raw_port, int) and raw_port > 0
                else parse_positive_int(str(raw_port), 5432)
            )
            raw_user = _config_get(self.config_service, "POSTGRES_USER")
            user = (
                str(raw_user).strip()
                if raw_user is not None and str(raw_user).strip()
                else None
            )
            raw_password = _config_get(self.config_service, "POSTGRES_PASSWORD")
            password = str(raw_password) if raw_password is not None else None
            raw_db = _config_get(self.config_service, "POSTGRES_DB")
            database = (
                str(raw_db).strip()
                if raw_db is not None and str(raw_db).strip()
                else None
            )

            self.pg_pool = PostgresPool(
                PostgresPoolOptions(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    max_size=1,
                    connection_timeout_ms=POSTGRES_CONNECT_TIMEOUT_MS,
                    idle_timeout_ms=30_000,
                )
            )
        return self.pg_pool

    async def ping_postgres(self) -> HealthIndicatorResult:
        try:
            pool = self._get_pg_pool()
            query_fn = getattr(pool, "query", None)
            if callable(query_fn):
                res = query_fn("SELECT 1")
                if inspect.isawaitable(res):
                    await res
            else:
                await pool.ping()
            return {"postgres": {"status": "up"}}
        except Exception as exc:
            message = str(exc) if str(exc) else "Connection failed"
            return {"postgres": {"status": "down", "message": message}}

    async def pingPostgres(self) -> HealthIndicatorResult:
        return await self.ping_postgres()


def create_health_router(
    controller: HealthController | None = None,
) -> APIRouter:
    """Create the FastAPI APIRouter for `/health`, `/health/ready`, `/health/live`."""
    router = APIRouter(tags=["health"])

    def _resolve_controller(request: Request) -> HealthController:
        if controller is not None:
            return controller
        app_ctrl: HealthController | None = getattr(
            request.app.state,
            "health_controller",
            None,
        )
        if app_ctrl is not None:
            return app_ctrl
        fallback = HealthController(
            config_service=getattr(request.app.state, "env", os.environ),
            shutdown_state=getattr(
                request.app.state,
                "shutdown_state",
                get_shutdown_state(),
            ),
            mongo_service=getattr(request.app.state, "mongo_service", None),
            redis_service=getattr(request.app.state, "redis_service", None),
        )
        request.app.state.health_controller = fallback
        return fallback

    @router.get("/health/live")
    @public
    @skip_rate_limit
    @skip_request_deadline
    @skip_country_access
    @skip_inbound_bulkhead
    @skip_tracing
    async def health_live(request: Request) -> dict[str, str]:
        ctrl = _resolve_controller(request)
        return ctrl.live()

    @router.get("/health")
    @public
    @skip_rate_limit
    @skip_request_deadline
    @skip_country_access
    @skip_inbound_bulkhead
    @skip_tracing
    async def health_check(request: Request) -> dict[str, Any]:
        ctrl = _resolve_controller(request)
        return await ctrl.check()

    @router.get("/health/ready")
    @public
    @skip_rate_limit
    @skip_request_deadline
    @skip_country_access
    @skip_inbound_bulkhead
    @skip_tracing
    async def health_ready(request: Request) -> dict[str, Any]:
        ctrl = _resolve_controller(request)
        return await ctrl.ready()

    return router


__all__ = [
    "DEFAULT_HEALTH_CACHE_TTL_MS",
    "DEFAULT_HEAP_THRESHOLD_BYTES",
    "DEFAULT_STORAGE_THRESHOLD_PERCENT",
    "POSTGRES_CONNECT_TIMEOUT_MS",
    "REDIS_PING_TIMEOUT_MS",
    "DiskHealthIndicator",
    "FeatureFlagHealthIndicator",
    "HealthCheckService",
    "HealthController",
    "HealthIndicatorFunction",
    "HealthIndicatorResult",
    "MemoryHealthIndicator",
    "MicroserviceHealthIndicator",
    "MongooseHealthIndicator",
    "ServiceUnavailableException",
    "create_health_router",
]
