from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from strawberry.fastapi import GraphQLRouter

from app.app_controller import app_router
from app.auth.access_token_authenticator import AccessTokenAuthenticator
from app.auth.auth_controller import AuthController, create_auth_router
from app.auth.auth_service import AuthService
from app.auth.guards.jwt_auth_guard import JwtAuthGuard
from app.common.config.parse_http_server_timeouts import (
    parse_http_server_timeouts,
)
from app.common.config.parse_trust_proxy import parse_trust_proxy
from app.common.config.parsers import (
    assert_websocket_production_origins,
    parse_inbound_bulkhead_config,
    parse_inbound_load_level_config,
    parse_non_negative_int,
    parse_positive_timeout_ms,
    parse_priority_shedding_config,
    parse_under_pressure_config,
    parse_websocket_config,
)
from app.common.config.resolve_coordination_storage import (
    RateLimitStorage,
    resolve_rate_limit_storage_from_config,
)
from app.common.config.validate_env import (
    is_conformance_harness_enabled,
    validate_env,
)
from app.common.filters.all_exceptions_filter import (
    AllExceptionsFilter,
    AllExceptionsMiddleware,
    register_exception_handlers,
)
from app.common.mongo.mongo_service import MongoService
from app.common.postgres.postgres_pool import PostgresPool
from app.common.redis.redis_service import RedisService
from app.common.uploads import uploads_router
from app.conformance import (
    ConformanceCacheService,
    ConformanceHttpClientService,
    conformance_cache_router,
    conformance_deadline_router,
    conformance_http_client_router,
    conformance_priority_router,
    conformance_rate_limit_router,
    conformance_redaction_router,
    set_conformance_cache_service,
    set_conformance_http_client_service,
)
from app.core.background_jobs import (
    JobEnqueueService,
    PostgresJobRepository,
)
from app.core.cache import CacheService, MemoryCacheBackend
from app.core.compression.compression_middleware import (
    DEFAULT_COMPRESSION_THRESHOLD,
    CompressionMiddleware,
)
from app.core.csrf.csrf_middleware import CsrfMiddleware, resolve_csrf_options
from app.core.csrf.csrf_token_service import CsrfTokenService
from app.core.feature_flag import feature_flag_router
from app.core.health.health_controller import (
    FeatureFlagHealthIndicator,
    HealthController,
    create_health_router,
)
from app.core.http_client import HttpClient
from app.core.logger.logger_config import configure_structlog
from app.core.logger.logger_middleware import LoggerMiddleware
from app.core.overload.priority_bulkhead_guard import (
    InboundBulkheadService,
    InboundPriorityBulkheadGuard,
)
from app.core.overload.under_pressure import UnderPressureMiddleware
from app.core.rate_limiter.guest_id_middleware import GuestIdMiddleware
from app.core.rate_limiter.rate_limit_guard import (
    RateLimitGuard,
    default_rate_limiter_options,
)
from app.core.rate_limiter.stores import (
    FallbackRateLimitBackend,
    MemoryRateLimitBackend,
    RateLimitBackend,
    RedisRateLimitBackend,
)
from app.core.request_deadline.deadline_context import (
    DEFAULT_REQUEST_TIMEOUT_MAX_MS,
    DEFAULT_REQUEST_TIMEOUT_MS,
)
from app.core.request_deadline.deadline_middleware import (
    RequestDeadlineMiddleware,
)
from app.core.shutdown.shutdown_state import ShutdownState, get_shutdown_state
from app.example import (
    example_captcha_router,
    example_jobs_router,
    example_notification_router,
    set_job_enqueue_service,
)
from app.graphql.schema import schema
from app.instrumentation import register_instrumentation
from app.realtime.socketio_server import create_socketio_app

HELMET_CSP_POLICY: Final[str] = (
    "default-src 'self' https://sandbox.embed.apollographql.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com "
    "https://sandbox.embed.apollographql.com; "
    "img-src 'self' data: validator.swagger.io "
    "https://sandbox.embed.apollographql.com "
    "https://apollo-server-landing-page.cdn.apollographql.com; "
    "script-src 'self' https: 'unsafe-inline' "
    "https://sandbox.embed.apollographql.com; "
    "base-uri 'self'; "
    "font-src 'self' https: data:; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "object-src 'none'; "
    "script-src-attr 'none'; "
    "upgrade-insecure-requests"
)

DEFAULT_SECURITY_HEADERS: Final[tuple[tuple[bytes, bytes], ...]] = (
    (b"content-security-policy", HELMET_CSP_POLICY.encode("latin-1")),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"origin-agent-cluster", b"?1"),
    (b"referrer-policy", b"no-referrer"),
    (
        b"strict-transport-security",
        b"max-age=15552000; includeSubDomains",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"x-dns-prefetch-control", b"off"),
    (b"x-download-options", b"noopen"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"x-permitted-cross-domain-policies", b"none"),
    (b"x-xss-protection", b"0"),
)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware applying helmet headers and stripping X-Powered-By."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers: list[tuple[bytes, bytes]] = list(
                    message.get("headers") or []
                )
                filtered: list[tuple[bytes, bytes]] = [
                    (k, v) for k, v in raw_headers if k.lower() != b"x-powered-by"
                ]
                existing_names = {k.lower() for k, _ in filtered}
                for header_key, header_val in DEFAULT_SECURITY_HEADERS:
                    if header_key not in existing_names:
                        filtered.append((header_key, header_val))
                message = {**message, "headers": filtered}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _resolve_cors_origins(raw_origins: str | None) -> list[str]:
    if not raw_origins or not raw_origins.strip():
        return ["*"]
    origins = [part.strip() for part in raw_origins.split(",") if part.strip()]
    return origins if origins else ["*"]


def create_app(
    env: Mapping[str, str | None] | None = None,
    *,
    validate_environment: bool = True,
    shutdown_state: ShutdownState | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application with NestJS parity middleware."""
    source_env: Mapping[str, str | None] = os.environ if env is None else env
    if validate_environment:
        validated_env = validate_env(source_env)
    else:
        validated_env = {k: str(v) for k, v in source_env.items() if v is not None}

    node_env = validated_env.get("NODE_ENV", "development")
    ws_config = parse_websocket_config(validated_env)
    assert_websocket_production_origins(ws_config, node_env)

    configure_structlog(validated_env)
    register_instrumentation(validated_env)

    bulkhead_config = parse_inbound_bulkhead_config(validated_env)
    load_level_config = parse_inbound_load_level_config(validated_env)
    bulkhead_service = InboundBulkheadService(bulkhead_config, load_level_config)
    bulkhead_guard = InboundPriorityBulkheadGuard(
        bulkhead_service,
        parse_priority_shedding_config(validated_env),
        load_level_config,
    )

    app = FastAPI(
        title="FastAPI Enterprise Backend Boilerplate",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    resolved_shutdown_state = shutdown_state or get_shutdown_state()
    app.state.shutdown_state = resolved_shutdown_state
    app.state.trust_proxy = parse_trust_proxy(validated_env.get("TRUST_PROXY"))
    app.state.http_server_timeouts = parse_http_server_timeouts(validated_env)
    app.state.env = validated_env

    mongo_service = MongoService(env=validated_env)
    redis_service = RedisService(env=validated_env)
    postgres_pool = PostgresPool(env=validated_env)
    csrf_token_service = CsrfTokenService()
    csrf_options = resolve_csrf_options(env=validated_env)
    feature_flag_health = FeatureFlagHealthIndicator()
    health_controller = HealthController(
        config_service=validated_env,
        shutdown_state=resolved_shutdown_state,
        feature_flag_health=feature_flag_health,
        mongo_service=mongo_service,
        redis_service=redis_service,
    )

    resolved_shutdown_state.register_before_close(mongo_service.close)
    resolved_shutdown_state.register_before_close(redis_service.on_module_destroy)
    resolved_shutdown_state.register_before_close(health_controller.on_module_destroy)
    resolved_shutdown_state.register_before_close(postgres_pool.close)

    app.state.mongo_service = mongo_service
    app.state.redis_service = redis_service
    app.state.postgres_pool = postgres_pool
    app.state.csrf_token_service = csrf_token_service
    app.state.feature_flag_health = feature_flag_health
    app.state.health_controller = health_controller

    # Background jobs
    job_repo = PostgresJobRepository(postgres_pool)
    job_enqueue = JobEnqueueService(job_repo)
    set_job_enqueue_service(job_enqueue)
    app.state.job_enqueue_service = job_enqueue

    auth_service = AuthService(
        mongo_service=mongo_service,
        redis_service=redis_service,
        config_service=validated_env,
    )
    auth_controller = AuthController(
        auth_service=auth_service,
        config_service=validated_env,
        csrf_token_service=csrf_token_service,
    )
    jwt_guard = JwtAuthGuard(
        auth_service=auth_service,
        redis_service=redis_service,
        config_service=validated_env,
    )

    rate_limit_storage = resolve_rate_limit_storage_from_config(validated_env)
    rate_limit_backend: RateLimitBackend
    if rate_limit_storage == RateLimitStorage.REDIS:
        raw_instances = (validated_env.get("HTTP_CLIENT_INSTANCE_COUNT") or "1").strip()
        instance_count = int(raw_instances) if raw_instances.isdigit() else 1
        raw_cooldown = validated_env.get("RATE_LIMIT_FALLBACK_COOLDOWN_MS")
        cooldown_ms = (
            int(raw_cooldown) if raw_cooldown and raw_cooldown.isdigit() else 5000
        )
        rate_limit_backend = FallbackRateLimitBackend(
            RedisRateLimitBackend(redis_service),
            MemoryRateLimitBackend(),
            instance_count=max(1, instance_count),
            cooldown_ms=cooldown_ms,
        )
    else:
        rate_limit_backend = MemoryRateLimitBackend()
    rate_limit_options = default_rate_limiter_options(storage=rate_limit_storage)
    rate_limit_guard = RateLimitGuard(rate_limit_backend, rate_limit_options)

    app.state.bulkhead_service = bulkhead_service
    app.state.auth_service = auth_service
    app.state.auth_controller = auth_controller
    app.state.rate_limit_backend = rate_limit_backend
    app.router.dependencies.extend(
        [
            Depends(bulkhead_guard),
            Depends(jwt_guard),
            Depends(rate_limit_guard),
        ]
    )

    exc_filter = AllExceptionsFilter()
    register_exception_handlers(app, exc_filter)

    default_timeout_ms = parse_positive_timeout_ms(
        validated_env.get("REQUEST_TIMEOUT_MS"),
        DEFAULT_REQUEST_TIMEOUT_MS,
    )
    parsed_max_timeout_ms = parse_positive_timeout_ms(
        validated_env.get("REQUEST_TIMEOUT_MAX_MS"),
        DEFAULT_REQUEST_TIMEOUT_MAX_MS,
    )
    max_timeout_ms = max(default_timeout_ms, parsed_max_timeout_ms)

    compression_threshold = parse_non_negative_int(
        validated_env.get("COMPRESSION_THRESHOLD"),
        DEFAULT_COMPRESSION_THRESHOLD,
    )
    cors_origins = _resolve_cors_origins(validated_env.get("ALLOWED_ORIGINS"))

    app.add_middleware(
        RequestDeadlineMiddleware,
        default_timeout_ms=default_timeout_ms,
        max_timeout_ms=max_timeout_ms,
    )
    app.add_middleware(
        AllExceptionsMiddleware,
        exception_filter=exc_filter,
    )
    app.add_middleware(
        CsrfMiddleware,
        csrf_token_service=csrf_token_service,
        options=csrf_options,
    )
    app.add_middleware(
        GuestIdMiddleware,
        cookie_secret=validated_env.get("COOKIE_SECRET"),
        node_env=node_env,
    )
    app.add_middleware(LoggerMiddleware)
    app.add_middleware(
        CompressionMiddleware,
        threshold=compression_threshold,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        UnderPressureMiddleware,
        config=parse_under_pressure_config(validated_env),
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # Base & Core Routers
    app.include_router(app_router)
    app.include_router(create_auth_router(auth_controller))
    app.include_router(create_health_router())
    app.include_router(feature_flag_router)
    app.include_router(uploads_router)

    # Example Routers
    app.include_router(example_captcha_router)
    app.include_router(example_notification_router)
    app.include_router(example_jobs_router)

    # GraphQL
    graphql_router = GraphQLRouter(schema)
    app.include_router(graphql_router, prefix="/graphql")

    # Conformance Harness (only registered when enabled)
    if is_conformance_harness_enabled(validated_env):
        cache_svc = CacheService(backend=MemoryCacheBackend())
        conformance_cache_svc = ConformanceCacheService(cache_svc)
        set_conformance_cache_service(conformance_cache_svc)

        http_client = HttpClient()
        conformance_http_svc = ConformanceHttpClientService(http_client)
        set_conformance_http_client_service(conformance_http_svc)

        app.include_router(conformance_cache_router)
        app.include_router(conformance_http_client_router)
        app.include_router(conformance_deadline_router)
        app.include_router(conformance_priority_router)
        app.include_router(conformance_rate_limit_router)
        app.include_router(conformance_redaction_router)

    # WebSockets / Socket.IO
    if ws_config.enabled:
        authenticator = AccessTokenAuthenticator(
            config_service=validated_env,
            redis_service=redis_service,
        )
        socket_app, gateway = create_socketio_app(
            ws_config,
            authenticator=authenticator,
            redis_service=redis_service,
            redis_url=validated_env.get("REDIS_URL"),
        )
        app.mount(ws_config.path, socket_app)
        app.state.realtime_gateway = gateway

    return app


def __getattr__(name: str) -> Any:
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_SECURITY_HEADERS",
    "HELMET_CSP_POLICY",
    "SecurityHeadersMiddleware",
    "create_app",
]
