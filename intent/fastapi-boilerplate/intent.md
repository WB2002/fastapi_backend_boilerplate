# Intent: FastAPI Enterprise Backend Boilerplate (NestJS Parity)
Author: Platform Engineering · Status: accepted · Date: 2026-11-19
Lifecycle: DRAFT

## Problem
We have a battle-tested NestJS 12 + Fastify backend boilerplate with 16 enterprise capability domains (core HTTP/ASGI server resilience, RS256 JWT + Argon2id bulkhead auth + CSRF, polyglot Postgres/Mongo/Redis persistence, multi-identifier rate limiting with cooldown failover, inbound overload & priority shedding, resilient outbound HTTP client with SSRF protection, multi-tier SWR caching with stampede protection, transactional background jobs with PostgreSQL ACID ledger, Socket.IO real-time WebSockets with Redis adapter, GraphQL with Redis complexity/concurrency budgets, GeoIP country access & ClamAV magic-byte uploads & hCaptcha, OpenTelemetry X-Ray + CloudWatch EMF + PII log redaction + Terminus health checks, dynamic Redis/Mongo feature flags, fail-open Slack notifications, conformance test harness, and an 8-tier verification pipeline). We need all of this functionality ported to Python 3.12+ and FastAPI using Python best practices while maintaining 100% functional and wire-level contract parity.

## User and evidence
Primary user: Backend & platform engineers delivering production FastAPI services.
Job to be done: Bootstrap a production-hardened FastAPI backend with complete feature, configuration (67 env vars), and wire-contract parity with the NestJS backend boilerplate.
Current workaround: Repository currently contains only the AI-SDLC control-plane scaffolding with no Python application source or package manifest.
Evidence: user-reported (extracted directly from the "Development Guidelines" Google Doc tabs `Backend Boilerplate` and `NestJS Boilerplate Features`, cross-checked against `/Users/pc/Documents/personal/nestjs_backend_boilerplate`).

## Proposed outcome
A complete, modular Python 3.12+ FastAPI backend boilerplate (`app/core/`, `app/common/`, `app/auth/`, `app/categories/`, `app/example/`, `app/conformance/`, `migrations/postgres/`, `tests/`, `scripts/`) managed by `uv` that implements every subsystem, environment variable, HTTP/GraphQL/WebSocket contract, and quality gate from the NestJS reference repository.

## Why now
Establishing the FastAPI boilerplate foundation unblocks Python-based microservices, AI/RAG backends, and enterprise APIs that require the same operational, security, and reliability guarantees as our Node.js/NestJS services.

## Scope
Must:
- **Domain 1 (Core HTTP & ASGI Runtime):** FastAPI + Uvicorn setup, declarative 67-env validator (`validate_env`), Brotli/Gzip compression (`COMPRESSION_THRESHOLD`), Helmet-equivalent security headers & CSP, HMAC-signed cookies (`COOKIE_SECRET`), CORS (`ALLOWED_ORIGINS`), `TRUST_PROXY` client IP resolution, ALB keep-alive (`65000ms`), request deadlines (`REQUEST_TIMEOUT_MS`, `REQUEST_TIMEOUT_MAX_MS`, `X-Request-Timeout-Ms`, `504`/`499`), global exception handler (`AllExceptionsFilter` JSON contract), and two-phase `SIGTERM` graceful shutdown (`SHUTDOWN_DRAIN_MS`, `SHUTDOWN_FORCE_MS`).
- **Domain 2 (Auth & CSRF):** RS256 JWT (`JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`), Argon2id hashing with bounded concurrency bulkhead (`AUTH_PASSWORD_HASH_MAX_CONCURRENT=2`, `MAX_QUEUE=32`, `MAX_WAIT_MS=2000`), Redis JTI revocation (`revoked_jti:*`) and refresh sessions, dual cookie/Bearer auth, RBAC guards (`USER`, `ADMIN`), and double-submit HMAC CSRF middleware (`/auth/csrf`, `X-CSRF-Token`, `CSRF_EXEMPT_PATHS`).
- **Domain 3 (Polyglot Persistence):** SQLAlchemy 2.0 Async + `asyncpg` pool & SQL migration runner (`migrations/postgres/001_create_country_access.sql`, `002_create_background_jobs.sql`), Motor + Beanie ODM with lazy non-blocking startup (`serverSelectionTimeoutMS=2000`), and `redis.asyncio` service with pub/sub client duplication.
- **Domain 4 (Rate Limiting):** Pluggable `memory` and `redis` fixed-window counter backends with cooldown failover (`RATE_LIMIT_FALLBACK_COOLDOWN_MS=5000`), identifiers (`Ip`, `UserId`, `DeviceFingerprint`, `GuestId`), and `GuestIdMiddleware`.
- **Domain 5 (Inbound Overload, Priority Shedding & Load Leveling):** Event-loop delay & RSS/heap pressure monitor (`UNDER_PRESSURE_*`), per-instance concurrency bulkhead (`INBOUND_MAX_IN_FLIGHT`), progressive API priority shedding (`critical`, `high`, `normal`, `low`), bounded FIFO load-leveling queue (`INBOUND_LOAD_LEVEL_*`), and `503` + `Retry-After` + CloudWatch EMF telemetry.
- **Domain 6 (Resilient Outbound HTTP Client):** Fluent `httpx.AsyncClient` builder with exponential backoff + full jitter, circuit breaker, fixed/AIMD adaptive concurrency limiter (`HTTP_CLIENT_INSTANCE_COUNT`), single-flight GET coalescing, deadline propagation, and DNS/IP SSRF blocking (`HTTP_CLIENT_ALLOW_PRIVATE_URLS`).
- **Domain 7 (Caching Layer & Stampede Protection):** `memory` and `redis` multi-tier cache with soft/hard TTL Stale-While-Revalidate (SWR), in-process single-flight deduplication, Redis distributed loader lock (`SET NX PX`), TTL jitter (`0.15`), and negative caching (`cache_null`).
- **Domain 8 (Background Jobs Engine):** PostgreSQL `background_jobs` ACID ledger (`FOR UPDATE SKIP LOCKED` claims, active `dedupe_key` partial unique index, `lease_until` timestamps) paired with **Celery** (`celery[redis]`) task dispatch (`wake_job_row` with exponential backoff) and periodic expired-lease reclaim sweep (`BACKGROUND_JOBS_LEASE_RECLAIM_*`).
- **Domain 9 (Real-Time WebSockets):** `python-socketio` (`AsyncServer` + `AsyncRedisManager`) mounted at `WEBSOCKET_PATH` (`/socket.io`) on `/realtime` namespace, JWT handshake verification, Redis per-user active connection cap (`5`), per-socket message rate limiter (`120/min`), graceful disconnect on shutdown, and `ExampleRealtimeGateway` (`join`, `message`, `admin-broadcast`).
- **Domain 10 (GraphQL API):** Strawberry GraphQL on `/graphql` with `Category` queries (`categories`, `category`, `searchCategories`), `QueryDepthLimiter(10)`, and custom `ComplexityExtension` enforcing max complexity (`50`), Redis per-user/guest 60s budget (`1000`), and Redis concurrency lease cap (`3`).
- **Domain 11 (Security, GeoIP, Uploads & Captcha):** Recursive pre-validation HTML input sanitization (`nh3`), MaxMind GeoLite2 `.mmdb` reader (`GEOIP_DATABASE_PATH`) + Postgres country rules cache (`CountryAccessGuard`), multipart upload streaming with size caps + `filetype` magic-byte inspection + ClamAV TCP `INSTREAM` virus scan, and outbound hCaptcha token verification (`CaptchaService`).
- **Domain 12 (Observability, Health & Telemetry):** Terminus-compatible `/health`, `/health/ready`, `/health/live` with `HealthSnapshot` cache (`2000ms`), OpenTelemetry SDK with AWS X-Ray ID generator & propagator, CloudWatch EMF metric emitter, `structlog` JSON logger with `X-Request-Id` correlation, and recursive PII redaction (`LOG_REDACTION_ENABLED`).
- **Domain 13 (Dynamic Feature Flags):** Redis-backed flag engine (`boolean`, percentage rollout, user allowlist) with local cache, Redis Pub/Sub invalidation, `45000ms` background reconciler, admin CRUD API (`FEATURE_FLAG_ADMIN_EMAILS`), and MongoDB `FeatureFlagAudit` logs.
- **Domain 14 (Notifications Service):** Fail-open `INotificationService` with async Slack Incoming Webhook delivery (`SLACK_WEBHOOK_URL`).
- **Domain 15 (Example & Conformance Harness):** `/example/*` endpoints and `/conformance/*` test harness (`cache`, `deadline`, `http-client`, `priority`, `rate-limit`, `redaction` — refused when `NODE_ENV=production`).
- **Domain 16 (8-Tier Testing & Quality Pipeline):** `scripts/verify.sh` running Ruff lint/format, `mypy --strict`, pytest unit tests, Testcontainers integration tests, Hypothesis property tests, GraphQL SDL & REST contract tests, Mutmut incremental mutation tests, and Semgrep/pip-audit/OSV/Gitleaks security scans.

Should:
- Provide a `docker-compose.yml` matching the local development stack (PostgreSQL, MongoDB, Redis, ClamAV optional profile) and `.env.example` documenting all 67 variables.

Later:
- Additional items from the general checklist marked `[rem]` or `[Optional]` that were not in the NestJS codebase (e.g., Stripe billing webhooks, gRPC server, PostgreSQL Row-Level Security policies).

Explicit exclusions:
- Frontend UI pages.
- Autonomous production deployment without human release approval.

## Outcome
Behavioral success metric:
- All 16 domains pass unit, integration (Testcontainers with live Postgres, Mongo, Redis), property (`hypothesis`), contract (GraphQL SDL snapshot + REST payload conformance), mutation (`mutmut`), and security (`semgrep`, `pip-audit`, `osv-scanner`, `gitleaks`) checks via `scripts/verify.sh`.
- All `/conformance/*` endpoints produce identical HTTP status codes, headers (`Retry-After`, `X-Request-Id`, `Set-Cookie`), and JSON bodies to the NestJS boilerplate.
Product risks:
- Event-loop blocking from CPU-bound operations (Argon2id hashing, GeoIP `.mmdb` reads, HTML sanitization) if not isolated. Mitigated via `asyncio.to_thread` + bounded concurrency bulkheads and Rust-backed extensions (`nh3`, `argon2-cffi`).
- Hang on startup or shutdown when MongoDB, PostgreSQL, or Redis is unreachable. Mitigated by lazy Mongo connection (`serverSelectionTimeoutMS=2000`), short pool connect timeouts, and bounded `SHUTDOWN_FORCE_MS=5000` teardown.
Assumptions requiring validation:
- Local and CI environments have Docker available for `testcontainers` integration tests.

## Affected systems
- `pyproject.toml`, `uv.lock`, `.env.example`, `docker-compose.yml`
- `app/` (`main.py`, `core/`, `common/`, `auth/`, `categories/`, `example/`, `conformance/`)
- `migrations/postgres/` (`001_create_country_access.sql`, `002_create_background_jobs.sql`)
- `tests/` (`unit/`, `integration/`, `property/`, `contracts/`)
- `scripts/` (`verify.sh`, `verify-full.sh`, `smoke-graceful-shutdown.sh`, `ci/*`)
- `AGENTS.md`, `docs/` (`product-brief.md`, `nfr.md`, `architecture.md`, `threat-model.md`, `observability.md`, `adr/0001-stack.md`)

## Constraints
- Experience: none (Backend API & infrastructure boilerplate; no user-facing visual UI screens, though HTTP/WS/GraphQL wire contracts are strictly specified).
- Strict 100% wire-level and environment-variable parity with `/Users/pc/Documents/personal/nestjs_backend_boilerplate`.
- Do not set Status to `accepted` — reserved for human owner approval.

## Open questions
None — all architectural and library decisions were resolved during the `/grill-me` design interview.
