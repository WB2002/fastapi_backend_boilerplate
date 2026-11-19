# ADR 0001: Python FastAPI Polyglot Backend Boilerplate Stack
Status: accepted

## Context
We need a production-grade Python/FastAPI backend boilerplate that achieves 100% functional and wire-level contract parity with our reference NestJS 12 + Fastify Backend Boilerplate across all 16 capability domains (67 environment variables, polyglot persistence, resilient HTTP client, inbound overload shedding, multi-tier SWR caching, transactional background jobs, Socket.IO real-time gateway, GraphQL with Redis complexity/concurrency budgets, GeoIP & ClamAV security, OpenTelemetry/EMF observability, dynamic feature flags, conformance harness, and an 8-tier verification pipeline).

## Decision
We standardize on the following Python 3.12+ technology stack:

1. **Runtime, Package Manager & HTTP Server:**
   - **Python 3.12+** managed via **`uv`** (`pyproject.toml` + `uv.lock`).
   - **FastAPI** + **Starlette** running on **Uvicorn** (`uvloop` + `httptools`) with configurable `HTTP_KEEP_ALIVE_TIMEOUT_MS` (`65000ms`), `TRUST_PROXY` CIDR parsing, Brotli (`brotli-asgi`) + Gzip compression (`COMPRESSION_THRESHOLD=1024`), security headers (`SecurityHeadersMiddleware` mirroring `@fastify/helmet`), HMAC-signed cookies (`itsdangerous` / `COOKIE_SECRET`), and two-phase `SIGTERM` drain/force shutdown (`SHUTDOWN_DRAIN_MS=10000`, `SHUTDOWN_FORCE_MS=5000`).
2. **Configuration & Input Validation:**
   - **Pydantic v2** + **`pydantic-settings`** paired with a declarative 67-variable schema validator (`validate_env`) that reports *all* missing required keys at startup and blocks `CONFORMANCE_HARNESS_ENABLED=true` when `NODE_ENV=production`.
   - **`nh3`** (Rust-backed Ammonia HTML sanitizer) pre-validation middleware/validators stripping disallowed HTML tags before Pydantic field length/format checks.
3. **Authentication, Authorization & CSRF:**
   - **PyJWT (`cryptography`)** for asymmetric **RS256** JWT signing (`JWT_PRIVATE_KEY`) and verification (`JWT_PUBLIC_KEY`).
   - **`argon2-cffi`** for Argon2id password hashing wrapped in an async concurrency bulkhead (`asyncio.Semaphore` + bounded wait queue: `AUTH_PASSWORD_HASH_MAX_CONCURRENT=2`, `MAX_QUEUE=32`, `MAX_WAIT_MS=2000`).
   - **Double-submit HMAC CSRF middleware** (`CsrfMiddleware`, `/auth/csrf`, `X-CSRF-Token`, `CSRF_EXEMPT_PATHS`).
4. **Polyglot Persistence (PostgreSQL, MongoDB, Redis):**
   - **PostgreSQL:** **SQLAlchemy 2.0 Async** + **`asyncpg`** pool (`country_access` rules and `background_jobs` ACID ledger) + SQL migration runner executing `migrations/postgres/*.sql`.
   - **MongoDB:** **Motor** (`motor.motor_asyncio`) + **Beanie ODM** (`User`, `Category`, `FeatureFlagAudit` documents) configured with non-blocking lazy connection (`serverSelectionTimeoutMS=2000`, `connectTimeoutMS=2000`).
   - **Redis:** **`redis.asyncio`** with connection pooling and `.duplicate()` pub/sub clients.
5. **Traffic Management, Overload & Caching:**
   - **Rate Limiting:** Pluggable `memory` and `redis` fixed-window counter stores with automatic cooldown fallback (`RATE_LIMIT_FALLBACK_COOLDOWN_MS=5000`) and identifiers (`Ip`, `UserId`, `DeviceFingerprint`, `GuestId` signed cookie).
   - **Inbound Overload Protection:** Event-loop lag monitor + `psutil` RSS/heap pressure monitor (`UNDER_PRESSURE_*`), per-instance concurrency bulkhead (`INBOUND_MAX_IN_FLIGHT`), progressive priority shedding (`@api_priority`: `critical`, `high`, `normal`, `low`), and bounded FIFO load-leveling wait queue (`INBOUND_LOAD_LEVEL_*`), returning `503` + `Retry-After`.
   - **Multi-Tier SWR Cache:** `memory` / `redis` cache with soft/hard TTL Stale-While-Revalidate, in-process single-flight future deduplication, distributed Redis loader lock (`SET NX PX`), TTL jitter (`0.15`), and negative caching (`cache_null`).
6. **Resilient Outbound HTTP Client:**
   - **`httpx.AsyncClient`** wrapped in a fluent `HttpClientBuilder` supporting exponential backoff with full jitter, sliding-window circuit breaker, fixed or AIMD adaptive concurrency limiting (scaled by `HTTP_CLIENT_INSTANCE_COUNT`), single-flight GET request coalescing, `X-Request-Deadline-Ms` / `asyncio.timeout` propagation, and pre-connect DNS/IP SSRF blocking (`ipaddress` private/loopback/link-local denial unless `HTTP_CLIENT_ALLOW_PRIVATE_URLS=true`).
7. **Background Jobs Engine (Celery + Redis Broker + PostgreSQL Ledger):**
   - **PostgreSQL `background_jobs` table** as the durable source of truth (`FOR UPDATE SKIP LOCKED` claims, partial unique active `dedupe_key` index, `lease_until` timestamps).
   - **Celery** (`celery[redis]`) as the distributed wake queue (`wake_job_row` task with exponential backoff), paired with an async periodic expired-lease reclaim task (`BACKGROUND_JOBS_LEASE_RECLAIM_*`).
8. **Real-Time WebSockets & GraphQL:**
   - **WebSockets:** **`python-socketio`** (`AsyncServer` + `ASGIApp` + `AsyncRedisManager`) mounted at `WEBSOCKET_PATH` (`/socket.io`) on the `/realtime` namespace with JWT handshake verification, Redis per-user connection cap (`5`), per-socket token-bucket message rate limiter (`120/min`), and `ExampleRealtimeGateway`.
   - **GraphQL:** **`strawberry-graphql[fastapi]`** mounted at `/graphql` with `QueryDepthLimiter(10)` and custom `ComplexityExtension` enforcing per-operation max complexity (`50`), Redis 60s rolling user/guest budget (`1000`), and Redis concurrent execution lease cap (`3`).
9. **Security, GeoIP, Uploads, Captcha, Feature Flags & Notifications:**
   - **GeoIP:** **`geoip2`** reading MaxMind `GeoLite2-Country.mmdb` (`GEOIP_DATABASE_PATH`) combined with cached Postgres country rules.
   - **Uploads:** Streaming multipart parser enforcing `UPLOAD_MAX_FILES`, `UPLOAD_MAX_FILE_SIZE`, `UPLOAD_MAX_TOTAL_SIZE`, **`filetype`** magic-byte inspection, and async TCP `INSTREAM` **ClamAV** virus scanning (`CLAMAV_HOST`, `CLAMAV_PORT`).
   - **Captcha & Notifications:** `CaptchaService` (hCaptcha verify via resilient HTTP client) and fail-open `SlackNotificationService` (`SLACK_WEBHOOK_URL`).
   - **Dynamic Feature Flags:** Redis hash storage + local in-memory cache + Redis Pub/Sub invalidation + `45000ms` background reconciler + MongoDB `FeatureFlagAudit` trail.
10. **Observability & 8-Tier Quality Pipeline:**
    - **Observability:** **`structlog`** JSON logger with correlation IDs and recursive PII redaction (`LOG_REDACTION_ENABLED`), **OpenTelemetry Python SDK** (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-sdk-extension-aws` X-Ray IDs/propagator), and CloudWatch EMF JSON metric emitter.
    - **8-Tier Quality Gates:** `ruff` (lint/format), `mypy --strict` (types), `pytest` + `pytest-asyncio` (unit), `testcontainers` (integration against live Postgres, Mongo, Redis), `hypothesis` (property tests), Strawberry SDL snapshot + REST schema checks (contract), `mutmut` (incremental mutation testing), and `semgrep` + `pip-audit` + `osv-scanner` + `gitleaks` (security & supply chain).

## Alternatives considered
- **PostgreSQL-only persistence (dropping MongoDB):** Rejected because the user requested 1:1 architectural and contract parity with the NestJS polyglot stores (Postgres + Mongo + Redis).
- **ARQ / Taskiq / python-bullmq instead of Celery:** Rejected per user selection of **Celery + Redis broker + PostgreSQL ACID ledger** for background job execution.
- **Native FastAPI WebSockets instead of `python-socketio`:** Rejected because `python-socketio` preserves 100% wire and room/event compatibility with NestJS Socket.IO clients on `/socket.io` and `/realtime`.
- **Ariadne instead of Strawberry GraphQL:** Rejected because Strawberry provides first-class Python type annotations, native FastAPI integration, custom `SchemaExtension` hooks for Redis complexity/concurrency budgets, and deterministic CLI SDL export.

## Consequences
- **Positive:** Complete drop-in functional and wire-protocol parity with the NestJS boilerplate while leveraging idiomatic, type-safe async Python libraries (`FastAPI`, `Pydantic v2`, `SQLAlchemy 2.0 Async`, `Beanie`, `Celery`, `Strawberry`, `python-socketio`, `httpx`, `structlog`).
- **Trade-offs:** Running three backing stores (Postgres, Mongo, Redis) plus optional Celery workers requires Docker Compose / Testcontainers for full integration verification, matching the exact operational footprint of the NestJS repository.
