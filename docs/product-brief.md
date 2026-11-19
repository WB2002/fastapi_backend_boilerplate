# Product brief

## Problem
Modern backend services require production-grade resilience, security perimeters, polyglot persistence, real-time capabilities, background job durability, and multi-tier verification out of the box. Rebuilding these primitives from scratch on every Python/FastAPI project introduces security gaps, operational fragility, and weeks of undifferentiated boilerplate work.

## Users
- **Primary user:** Platform and backend engineers building high-reliability B2B, healthcare, and enterprise APIs in Python/FastAPI.
- **Downstream consumers:** Web and mobile clients, partner integrations, and internal microservices interacting over REST, GraphQL (`/graphql`), and Socket.IO WebSockets (`/socket.io` -> `/realtime`).

## Success metrics
- **100% functional and wire-level contract parity** with the reference NestJS Backend Boilerplate across all 16 capability domains (67 environment variables, HTTP status codes, headers, cookie semantics, Terminus health payload shape, GraphQL SDL, Socket.IO events, and `/conformance/*` harness routes).
- **8-tier quality gate (`scripts/verify.sh` / `quality`)** passing deterministically: Ruff lint/format, `mypy --strict`, pytest unit tests, Testcontainers integration tests (PostgreSQL, MongoDB, Redis), Hypothesis property tests, GraphQL SDL & REST contract snapshot tests, Mutmut incremental mutation tests, and Semgrep/pip-audit/OSV/Gitleaks security scans.
- **Zero unauthenticated or unsanitized entry points** by default (global JWT guard with explicit `@public` opt-out, double-submit CSRF cookie enforcement, recursive HTML/XSS input sanitization, magic-byte file inspection, and MaxMind GeoIP filtering).

## Scope
1. **Core HTTP & ASGI Runtime (FastAPI + Uvicorn):** Declarative 67-variable fail-fast env validator, Brotli/Gzip compression (`COMPRESSION_THRESHOLD`), Helmet-equivalent CSP/security headers, HMAC-signed cookies (`COOKIE_SECRET`), CORS, trusted proxy CIDR parsing (`TRUST_PROXY`), keep-alive timeout (`65000ms` > ALB 60s), per-request deadlines (`REQUEST_TIMEOUT_MS`, `X-Request-Timeout-Ms`, `499`/`504` semantics), and two-phase `SIGTERM` graceful shutdown (`SHUTDOWN_DRAIN_MS`, `SHUTDOWN_FORCE_MS`).
2. **Authentication & Authorization:** RS256 asymmetric JWTs (`JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`), Argon2id password hashing with a bounded async concurrency bulkhead (`AUTH_PASSWORD_HASH_MAX_CONCURRENT`, `MAX_QUEUE`, `MAX_WAIT_MS`), Redis JTI revocation (`revoked_jti:*`) and refresh token rotation, signed cookie + Bearer dual transport, role guards (`USER`, `ADMIN`), and double-submit HMAC CSRF middleware (`/auth/csrf`, `X-CSRF-Token`).
3. **Polyglot Persistence (PostgreSQL, MongoDB, Redis):** SQLAlchemy 2.0 Async (`asyncpg`) + raw SQL migration runner (`migrations/postgres/`), Motor + Beanie ODM with lazy non-blocking startup (`serverSelectionTimeoutMS=2000`), and `redis.asyncio` connection manager with pub/sub client duplication.
4. **Rate Limiting & Traffic Management:** Pluggable `memory` and `redis` fixed-window counter backends with automatic cooldown failover (`RATE_LIMIT_FALLBACK_COOLDOWN_MS`), tracking by IP, `UserId`, device fingerprint, or signed `guest_id` cookie (`GuestIdMiddleware`).
5. **Inbound Overload, Priority Shedding & HTTP Load Leveling:** Event-loop lag and RSS/heap pressure monitor (`UNDER_PRESSURE_*`), per-instance concurrency bulkhead (`INBOUND_MAX_IN_FLIGHT`), progressive API priority shedding (`critical`, `high`, `normal`, `low`), bounded FIFO load-leveling wait queue, and `503 Service Unavailable` with `Retry-After` and CloudWatch EMF overload metrics.
6. **Resilient Outbound HTTP Client:** Fluent `httpx.AsyncClient` builder with exponential backoff + full jitter retries, sliding-window circuit breaker, fixed or AIMD adaptive concurrency limiting (cluster-aware via `HTTP_CLIENT_INSTANCE_COUNT`), single-flight GET coalescing, deadline header propagation, and DNS/IP SSRF blocking (`HTTP_CLIENT_ALLOW_PRIVATE_URLS`).
7. **Caching Layer & Stampede Protection:** Multi-tier (`memory` / `redis`) cache with soft/hard TTL Stale-While-Revalidate (SWR), in-process single-flight deduplication, Redis distributed loader locks (`SET NX PX`), TTL jitter (`CACHE_TTL_JITTER_RATIO`), and negative caching (`cache_null`).
8. **Background Jobs Engine (Celery + Redis Broker + PostgreSQL ACID Ledger):** Transactional `background_jobs` PostgreSQL table (`FOR UPDATE SKIP LOCKED` claims, partial unique active `dedupe_key` index), Celery Redis task dispatch (`wake_job_row` with exponential backoff), and periodic expired-lease reclaim sweep (`BACKGROUND_JOBS_LEASE_RECLAIM_*`).
9. **Real-Time WebSockets (`python-socketio` + Redis Adapter):** Socket.IO ASGI server at `WEBSOCKET_PATH` (`/socket.io`) on `/realtime` namespace, Redis pub/sub adapter, JWT handshake authenticator, Redis per-user active connection limiter (`WEBSOCKET_MAX_CONNECTIONS_PER_USER=5`), per-socket message rate limiter (`120/min`), and `ExampleRealtimeGateway` (`join`, `message`, `admin-broadcast`).
10. **GraphQL API (Strawberry FastAPI):** Mounted at `/graphql` with `Category` schema (`categories`, `category`, `searchCategories`), `QueryDepthLimiter(10)`, and custom `ComplexityExtension` enforcing per-query max complexity (`50`), Redis per-user/guest 60s rolling complexity budget (`1000`), and Redis concurrent execution lease cap (`3`).
11. **Security, GeoIP, File Uploads & Bot Protection:** Recursive input HTML sanitization (`nh3`/`bleach`), MaxMind GeoLite2 `.mmdb` reader + Postgres country rules cache (`CountryAccessGuard`), streaming multipart uploads with magic-byte verification and ClamAV `INSTREAM` TCP virus scan, and outbound hCaptcha token verification (`CaptchaService`).
12. **Observability, Health & Telemetry:** Terminus-compatible `/health`, `/health/ready`, and `/health/live` endpoints with `HealthSnapshot` caching (`2000ms`), OpenTelemetry SDK with AWS X-Ray ID generator and propagator, CloudWatch Embedded Metric Format (EMF) JSON logger, structured JSON logging (`structlog`) with correlation IDs (`X-Request-Id`), and recursive PII log redaction.
13. **Dynamic Feature Flags:** Redis-backed flag evaluation engine (`boolean`, percentage rollout via consistent hashing, user allowlist) with in-memory cache, Redis pub/sub invalidation, background reconciliation loop (`45000ms`), admin CRUD API (`FEATURE_FLAG_ADMIN_EMAILS`), and MongoDB `FeatureFlagAudit` collection.
14. **Notifications Service:** Fail-open `INotificationService` abstraction with async Slack Incoming Webhook provider (`SLACK_WEBHOOK_URL`).
15. **Example & Conformance Harness Modules:** Reference endpoints (`/example/*`) and non-production verification routes (`/conformance/*`, blocked when `NODE_ENV=production`).
16. **Multi-Tier Testing & Quality Gates:** `uv`, `ruff`, `mypy --strict`, `pytest`, `testcontainers`, `hypothesis`, GraphQL SDL snapshot checks, `mutmut`, `semgrep`, `pip-audit`, `osv-scanner`, and `gitleaks`.

## Non-goals
- Frontend UI or client SDK generation beyond GraphQL SDL snapshots.
- Replacing the AI-SDLC control-plane skills in `.cursor/skills/`.
- Multi-region active-active database replication setup (handled at infrastructure layer).
