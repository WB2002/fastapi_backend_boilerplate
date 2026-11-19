# Spec: FastAPI Enterprise Backend Boilerplate (NestJS Parity)
Source: intent/fastapi-boilerplate/intent.md (pending human acceptance)
Lifecycle: DRAFT

## Requirements

### REQ-001: Declarative Environment Validation & Fail-Fast Boot
Experience: none
Given: The application starts with environment variables loaded from `.env` or process environment
When: `validate_env()` executes during bootstrap
Then: It validates all 67 schema keys, raises an error listing **every** missing or blank required key (`JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`, `COOKIE_SECRET`, `ACCESS_TOKEN_TTL`, `SESSION_TTL`, `MONGO_URI`, `REDIS_HOST`, `REDIS_PORT`, `GEOIP_DATABASE_PATH`, `HCAPTCHA_SECRET`) in a single error message (never first-failure-only), and raises an error if `NODE_ENV=production` while `CONFORMANCE_HARNESS_ENABLED=true`.

Acceptance criteria:
- Missing multiple required variables lists all missing keys in deterministic order.
- `NODE_ENV=production` + `CONFORMANCE_HARNESS_ENABLED=true` aborts startup immediately.

### REQ-002: Core HTTP Server, Security Headers, Compression, Cookies & Graceful Shutdown
Experience: none
Given: A running FastAPI/Uvicorn instance
When: Requests arrive or the process receives `SIGTERM` / `SIGINT`
Then:
- Security headers matching `@fastify/helmet` (CSP allowing Apollo sandbox, `X-Content-Type-Options: nosniff`, frameguard, hidden `X-Powered-By`) and CORS (`ALLOWED_ORIGINS`, `credentials=True`) are applied.
- Responses larger than `COMPRESSION_THRESHOLD` (default `1024` bytes) are compressed using Brotli (`br` preferred) or Gzip (`gzip` fallback).
- Cookies are parsed and HMAC-signed using `COOKIE_SECRET`.
- Client IP resolution respects `TRUST_PROXY` (`false` ignores `X-Forwarded-For`; `true` or CIDR list parses trusted proxy hops).
- On `SIGTERM`/`SIGINT`, `ShutdownState.is_shutting_down` flips to `True` immediately (causing `/health/ready` and `/health/live` to return `503`), the server waits `SHUTDOWN_DRAIN_MS` (`10000ms`) for ALB draining, then closes WebSockets, Celery producer, and DB pools within `SHUTDOWN_FORCE_MS` (`5000ms`).

Acceptance criteria:
- Keep-alive timeout defaults to `HTTP_KEEP_ALIVE_TIMEOUT_MS=65000ms`.
- `SIGTERM` flips `/health/ready` to `503` before closing active sockets.

### REQ-003: Request Deadlines, Abort Propagation & Global Exception Filter
Experience: none
Given: An inbound HTTP request with default timeout (`REQUEST_TIMEOUT_MS=30000`), route `@request_timeout(ms)`, or client header `X-Request-Timeout-Ms` (clamped to `REQUEST_TIMEOUT_MAX_MS=120000`)
When: The deadline expires, the client disconnects early, or an exception is raised
Then:
- A request-scoped `RequestDeadlineContext` (`ContextVar`) tracks remaining budget and triggers cancellation.
- Timeout expiration maps to HTTP `504 Gateway Timeout` (`message: "Request deadline exceeded"`).
- Client disconnect aborts downstream work and maps to HTTP `499 Client Closed Request` (`message: "Request cancelled by client"`).
- `AllExceptionsFilter` formats all HTTP errors as `{ statusCode, timestamp, path, requestId, message }` (or Terminus `{ statusCode, timestamp, path, requestId, status, info, error, details }` for health 503s) and maps MongoDB/Postgres connection timeouts to HTTP `503` (`"Service temporarily unavailable"`) while emitting `DependencyUnavailable503` EMF metrics.

Acceptance criteria:
- Route and header timeouts cannot exceed `REQUEST_TIMEOUT_MAX_MS`.
- All error responses include `requestId` matching `X-Request-Id` (or generated UUIDv4).

### REQ-004: Authentication, Argon2id Bulkhead, Redis JTI Revocation & Double-Submit CSRF
Experience: none
Given: The `AuthModule` and `CsrfMiddleware`
When: Clients call `/auth/csrf`, `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, or protected endpoints
Then:
- Global `JwtAuthGuard` protects all routes by default unless decorated with `@public`; accepts JWT from `access_token` cookie or `Authorization: Bearer <token>`, verifies RS256 signature with `JWT_PUBLIC_KEY`, and checks Redis `revoked_jti:<jti>` does not exist.
- Password hashing/verification uses Argon2id (`argon2-cffi`) guarded by a concurrency bulkhead (`AUTH_PASSWORD_HASH_MAX_CONCURRENT=2`, `AUTH_PASSWORD_HASH_MAX_QUEUE=32`, `AUTH_PASSWORD_HASH_MAX_WAIT_MS=2000`), rejecting excess load with HTTP `503`.
- `/auth/login` and `/auth/refresh` issue RS256 `accessToken` (`ACCESS_TOKEN_TTL=900s`), opaque/session `sessionId` (`SESSION_TTL=604800s`), and `csrfToken`, setting `HttpOnly` cookies (`access_token` on `/`, `session_id` on `/auth/refresh`) and `csrf_token` cookie.
- `/auth/logout` revokes the session/JTI in Redis (`revoked_jti:<id>` with TTL) and clears auth cookies.
- `CsrfMiddleware` validates that state-changing requests (`POST`, `PUT`, `PATCH`, `DELETE`) outside `CSRF_EXEMPT_PATHS` carry matching HMAC-valid `csrf_token` cookie and `X-CSRF-Token` header when `CSRF_ENABLED=true`.

Acceptance criteria:
- Revoked JTIs are rejected with HTTP `401 Unauthorized`.
- State-changing requests without a valid matching `X-CSRF-Token` header and `csrf_token` cookie fail with HTTP `403 Forbidden`.
- Concurrent Argon2id operations beyond `MAX_CONCURRENT + MAX_QUEUE` or exceeding `MAX_WAIT_MS` fail fast with HTTP `503`.

### REQ-005: Polyglot Persistence (PostgreSQL, MongoDB Lazy Connect, Redis)
Experience: none
Given: Configured `MONGO_URI`, `POSTGRES_*`, and `REDIS_*` environment variables
When: The application boots and serves requests
Then:
- MongoDB (Motor + Beanie) initializes with non-blocking lazy connection (`serverSelectionTimeoutMS=2000`, `connectTimeoutMS=2000`) so the HTTP server boots even if MongoDB is down (`GET /health` reports `mongo: down` with HTTP `503`).
- PostgreSQL (SQLAlchemy 2.0 Async + `asyncpg`) manages connection pooling and runs SQL migrations (`001_create_country_access.sql`, `002_create_background_jobs.sql`).
- Redis (`redis.asyncio`) provides the primary client and `.duplicate()` clients for Pub/Sub subscribers.

Acceptance criteria:
- Application boots and serves `/health/live` (`200`) even when MongoDB is offline at startup.
- Unhandled background database connection errors are logged and never crash the process.

### REQ-006: Multi-Identifier Rate Limiting with Redis-to-Memory Cooldown Failover
Experience: none
Given: `RateLimiterModule` configured with `RATE_LIMIT_STORAGE` (`memory` or `redis`) and `RATE_LIMIT_FALLBACK_COOLDOWN_MS=5000`
When: Requests hit global or route-level `@rate_limit(identifier, duration_ms, limit, cost)` rules
Then:
- Identifiers resolve via `Ip` (respecting `TRUST_PROXY`), `UserId` (`req.state.user.id`), `DeviceFingerprint` (hashed UA/headers), or `GuestId` (HMAC-signed `guest_id` cookie managed by `GuestIdMiddleware`).
- Fixed-window counter increments by `cost`; exceeding `limit` within `duration_ms` returns HTTP `429 Too Many Requests` with `Retry-After` and rate-limit headers.
- If `redis` storage throws a connection/timeout error, the limiter falls back to in-memory counters for `RATE_LIMIT_FALLBACK_COOLDOWN_MS` before probing Redis again.

Acceptance criteria:
- Redis outage during rate limiting degrades gracefully to in-memory rate limiting without throwing 500 errors.

### REQ-007: Inbound Overload Protection, Priority Shedding & HTTP Load Leveling
Experience: none
Given: Inbound requests arriving under load
When: Any overload mechanism is enabled (`UNDER_PRESSURE_ENABLED`, `INBOUND_BULKHEAD_ENABLED`, `INBOUND_PRIORITY_SHEDDING_ENABLED`, `INBOUND_LOAD_LEVELING_ENABLED`)
Then:
- **Under-pressure monitor:** Samples asyncio event-loop lag and process RSS/heap memory; if lag exceeds `UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS` (`1000ms`) or memory caps, sheds traffic with HTTP `503` and `Retry-After: <OVERLOAD_RETRY_AFTER_SEC>`.
- **Priority shedding:** Routes carry `@api_priority(level)` (`critical`, `high`, `normal`, `low`; default `INBOUND_DEFAULT_API_PRIORITY=normal`). As in-flight utilization crosses configured thresholds (`INBOUND_PRIORITY_SHED_LOW_AT`, `NORMAL_AT`, `HIGH_AT`, `CRITICAL_AT` of `INBOUND_MAX_IN_FLIGHT`), lower-priority requests are shed first with HTTP `503`, `Retry-After`, and `ApiPriorityShed503` EMF metric.
- **Load leveling & Bulkhead:** When `INBOUND_LOAD_LEVELING_ENABLED=true`, excess requests enter a bounded queue (`INBOUND_LOAD_LEVEL_MAX_QUEUE=100`) waiting up to `INBOUND_LOAD_LEVEL_MAX_WAIT_MS=2000ms` before failing with `503` (`InboundLoadLevelQueueFull503` or `InboundLoadLevelWaitTimeout503`). When slots are exhausted, `InboundBulkhead503` is returned.

Acceptance criteria:
- `/health*` routes bypass bulkhead, priority shedding, rate limiting, and request deadlines.
- Shed requests always include `Retry-After` header and record the corresponding EMF metric.

### REQ-008: Resilient Outbound HTTP Client with SSRF Protection
Experience: none
Given: An outbound client constructed via `HttpClientFactory.create_external(name, base_url)` or `create_internal(name, base_url)`
When: Outbound HTTP calls execute
Then:
- **SSRF defense:** If `HTTP_CLIENT_ALLOW_PRIVATE_URLS=false` (default), resolves target hostname and blocks private (RFC1918), loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`), and metadata IPs before opening a socket.
- **Resilience pipeline:** Composes single-flight GET coalescing, fixed or AIMD adaptive concurrency limiting (divided across `HTTP_CLIENT_INSTANCE_COUNT`), sliding-window circuit breaker (`OPEN`, `HALF_OPEN`, `CLOSED`), exponential backoff with full jitter retries, and inbound request deadline propagation (`AbortError` / `asyncio.TimeoutError`).

Acceptance criteria:
- Requests to `http://127.0.0.1` or `http://169.254.169.254` are blocked with an SSRF error when `HTTP_CLIENT_ALLOW_PRIVATE_URLS=false`.
- Concurrent identical GET requests coalesce into a single upstream call when coalescing is enabled.

### REQ-009: Multi-Tier Caching Layer & Stampede Protection
Experience: none
Given: `CacheService.get_or_set(key, loader, options)` backed by `memory` or `redis` (`CACHE_STORAGE`)
When: Cache hits, soft-TTL expirations, or cold misses occur under concurrency
Then:
- **TTL Jitter:** Applies random jitter (`CACHE_TTL_JITTER_RATIO=0.15`) to soft and hard TTLs to prevent synchronized cache avalanche.
- **Stale-While-Revalidate (SWR):** Between `soft_ttl_ms` and `hard_ttl_ms`, returns the stale value immediately while triggering a non-blocking background refresh.
- **Stampede Protection:** Coalesces concurrent in-process misses onto a single `asyncio.Future` and acquires a distributed Redis lock (`SET <lock_key> <token> NX PX <CACHE_LOADER_LOCK_TTL_MS=30000>`). Parallel nodes wait up to `CACHE_LOADER_WAIT_MS=5000ms` for the lock holder to populate the cache.
- **Negative Caching:** When `cache_null=True` and the loader returns `None`, caches a sentinel value using `CACHE_NEGATIVE_SOFT_TTL_MS` (`60000`) and `CACHE_NEGATIVE_HARD_TTL_MS` (`300000`).

Acceptance criteria:
- 50 concurrent calls to `get_or_set` on a cold key invoke the underlying loader function exactly once.

### REQ-010: Transactional Background Jobs Engine (Celery + Redis + PostgreSQL Ledger)
Experience: none
Given: `BACKGROUND_JOBS_ENABLED=true` and PostgreSQL `background_jobs` table
When: `JobEnqueueService.enqueue(input)` is called or a worker processes jobs (`BACKGROUND_WORKER_ENABLED=true`)
Then:
- **Durable Enqueue & Deduplication:** Inserts a `pending` row into PostgreSQL `background_jobs`. If an active (`pending` or `processing`) row with the same `dedupe_key` exists (`uq_background_jobs_dedupe_active`), returns `{ jobRowId: "", enqueued: false, deduplicated: true }`.
- **Celery Wake Dispatch:** Enqueues a Celery task (`background_jobs.run_job`, `task_id=job_row_id`, countdown/ETA from `run_at`, exponential backoff up to `max_attempts`) on Redis. If Redis enqueue fails, logs a warning without losing the committed PostgreSQL row.
- **Atomic Claim & Execution:** The worker claims the row in PostgreSQL using `SELECT ... FOR UPDATE SKIP LOCKED` (setting `status='processing'`, `locked_by`, `locked_at=NOW()`, `lease_until=NOW() + BACKGROUND_JOBS_CLAIM_LEASE_SEC`, `attempts = attempts + 1`), dispatches to `JobHandlerRegistry`, and marks `completed` or `failed` (with `last_error`).
- **Orphan Lease Reclaim:** When `BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED=true`, a periodic sweep (`BACKGROUND_JOBS_LEASE_RECLAIM_INTERVAL_MS=30000`) resets expired `processing` rows (`lease_until < NOW()`) back to `pending` (or `failed` if `attempts >= max_attempts`) and re-wakes them in Celery.

Acceptance criteria:
- Crashed worker leases are reclaimed automatically after `BACKGROUND_JOBS_CLAIM_LEASE_SEC` and re-dispatched via Celery.
- Duplicate active `dedupe_key` submissions are idempotently ignored at the PostgreSQL index level.

### REQ-011: Real-Time WebSockets (`python-socketio` + Redis Adapter)
Experience: none
Given: `WEBSOCKET_ENABLED=true` and `python-socketio` mounted at `WEBSOCKET_PATH` (`/socket.io`) on namespace `/realtime`
When: Clients connect, join rooms, send messages, or broadcast
Then:
- **Production Origin Guard:** Refuses startup if `NODE_ENV=production` and `ALLOWED_ORIGINS` is wildcard `*`.
- **Handshake Auth & Connection Cap:** Extracts JWT from `access_token` cookie, `Authorization` header, or `auth.token` (or query token only when `WEBSOCKET_ALLOW_QUERY_TOKEN=true`), verifies RS256 + Redis JTI revocation, and acquires a per-user connection slot in Redis (`WEBSOCKET_MAX_CONNECTIONS_PER_USER=5`, TTL `WEBSOCKET_CONNECTION_KEY_TTL_SEC=86400`). Disconnects immediately if unauthorized or over limit; releases slot on disconnect.
- **Inbound Message Rate Limit:** Enforces `WEBSOCKET_MESSAGES_PER_MINUTE=120` per socket/user.
- **Events (`ExampleRealtimeGateway`):**
  - `join`: Allows joining only the caller's own room `user:<user.id>` (`403` on foreign room).
  - `message`: Validates & sanitizes `ExampleRealtimeMessageDto` and emits `message` (`{ text, from }`) to `user:<user.id>`.
  - `admin-broadcast`: Enforces `UserRole.ADMIN` and broadcasts `admin-broadcast` (`{ text }`) across all nodes via `AsyncRedisManager`.

Acceptance criteria:
- Connecting a 6th concurrent socket for the same user when `WEBSOCKET_MAX_CONNECTIONS_PER_USER=5` disconnects the socket.
- `join` with a foreign `user:<other_id>` room is rejected with forbidden error.

### REQ-012: GraphQL API (Strawberry) with Depth, Complexity, Budget & Concurrency Throttling
Experience: none
Given: Strawberry GraphQL mounted at `/graphql` with `Category` schema (`categories`, `category(id)`, `searchCategories(filter)`)
When: A GraphQL operation is executed
Then:
- **Depth Limit:** Rejects queries deeper than `10` levels (`QueryDepthLimiter(10)`).
- **Redis Concurrency Lease:** Acquires a per-subject (`userId` or `guestId` or `anon`) Redis concurrency slot up to `GQL_MAX_CONCURRENCY=3` (TTL `GQL_CONCURRENCY_KEY_TTL_SEC=300`). If exceeded, sets `Retry-After` header, records `GraphqlConcurrencyDenied` EMF metric, and raises a GraphQL error (`OVERLOADED` / `TOO_MANY_REQUESTS`). Always releases the lease in `finally`.
- **Complexity & 60s Rolling Budget:** Computes operation AST complexity; rejects with `QUERY_TOO_COMPLEX` if `complexity > GQL_MAX_COMPLEXITY` (`50`). Consumes `complexity` from the subject's 60-second Redis budget (`GQL_USER_BUDGET=1000`); rejects with `OUT_OF_BUDGET` if remaining budget is insufficient.
- **OpenTelemetry:** Annotates the active span with `graphql.operation.name`.

Acceptance criteria:
- Queries exceeding complexity 50 return `extensions.code == "QUERY_TOO_COMPLEX"`.
- Cumulative queries exceeding 1000 complexity within 60s return `extensions.code == "OUT_OF_BUDGET"`.
- Exported Strawberry SDL matches `test/contracts/graphql/schema.snapshot.graphql`.

### REQ-013: Edge Security — HTML Sanitization, GeoIP Country Access, Uploads (Magic-Byte + ClamAV) & hCaptcha
Experience: none
Given: Inbound requests across REST and GraphQL
When: Payloads are parsed, country rules are checked, files are uploaded, or captcha tokens are verified
Then:
- **Input Sanitization:** All incoming string fields are sanitized of HTML/XSS tags (`nh3`) *before* Pydantic min/max length and format validators execute.
- **Country Access (`CountryAccessGuard`):** Resolves client IP via MaxMind GeoLite2 database (`GEOIP_DATABASE_PATH`), compares against PostgreSQL `country_access_rules` cached in memory (`COUNTRY_ACCESS_REFRESH_INTERVAL_MS`), and applies `COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY` (`block` or `allow`), bypassing `COUNTRY_ACCESS_BYPASS_PATHS` and `@skip_country_access` routes.
- **File Uploads (`UploadsService`):** Enforces `UPLOAD_MAX_FILES=5`, `UPLOAD_MAX_FILE_SIZE=5000000`, `UPLOAD_MAX_TOTAL_SIZE=20000000`, verifies file extension matches actual magic-byte signature (`filetype`), and when `UPLOAD_VIRUS_SCAN=true`, streams the buffer to ClamAV (`CLAMAV_HOST:CLAMAV_PORT`) via TCP `zINSTREAM` before accepting.
- **hCaptcha (`CaptchaService`):** Verifies client tokens against `HCAPTCHA_VERIFY_BASE_URL` using `HCAPTCHA_SECRET` via the resilient outbound HTTP client.

Acceptance criteria:
- Uploading a file whose magic bytes do not match its extension/MIME type returns HTTP `400 Bad Request`.
- Disallowed country IPs receive HTTP `403 Forbidden`.

### REQ-014: Observability — Terminus Health Probes, OpenTelemetry X-Ray, CloudWatch EMF & PII Redaction
Experience: none
Given: Health endpoints and telemetry configuration (`OTEL_ENABLED`, `APP_METRICS_ENABLED`, `LOG_REDACTION_ENABLED`)
When: Probes are queried or logs/metrics/traces are emitted
Then:
- `GET /health/live` returns `{ "status": "ok" }` (`200`) or `503` during shutdown.
- `GET /health` and `GET /health/ready` evaluate MongoDB ping, memory heap (`<150MB`), disk storage (`<90%` on `/`), feature flags (informational), Redis ping (when `REDIS_HOST` set), and PostgreSQL `SELECT 1` (when `POSTGRES_DB` set), caching results for `HEALTH_CHECK_CACHE_TTL_MS=2000ms` via `HealthSnapshot` and returning Terminus JSON `{ status, info, error, details }`.
- When `OTEL_ENABLED=true`, exports spans via OTLP HTTP (`OTEL_EXPORTER_OTLP_ENDPOINT`) using AWS X-Ray trace ID format and header propagation.
- When `APP_METRICS_ENABLED=true`, emits valid AWS CloudWatch Embedded Metric Format (`_aws` metadata block) JSON lines for all 503 overload, health check, and feature flag events.
- When `LOG_REDACTION_ENABLED=true`, recursively masks sensitive dictionary keys and token/email patterns in all log payloads.

Acceptance criteria:
- Concurrent bursts to `GET /health` within `2000ms` execute underlying DB pings only once (`HealthSnapshot` single-flight + TTL cache).
- Sensitivity tests confirm passwords, JWTs, cookies, and emails are replaced with `[REDACTED]` in logs.

### REQ-015: Dynamic Feature Flags with Redis Pub/Sub, Reconciler & MongoDB Audit Log
Experience: none
Given: `FeatureFlagModule` configured with `FEATURE_FLAG_ENV`, `FEATURE_FLAG_RECONCILE_MS=45000`, `FEATURE_FLAG_ADMIN_EMAILS`, and `FEATURE_FLAG_METRICS`
When: `FeatureFlagService.is_enabled(key, context)` or admin CRUD endpoints are called
Then:
- Evaluates flags from an in-memory cache synchronized with Redis (`ff:<env>:flags`), supporting boolean toggles, user ID allowlists, and deterministic percentage rollouts.
- Admin mutations (`POST`, `PUT`, `DELETE`) require an authenticated user whose email is in `FEATURE_FLAG_ADMIN_EMAILS` (or `ADMIN` role), persist to Redis, publish an invalidation event on Redis Pub/Sub (`FeatureFlagSubscriber`), and write an immutable audit entry to MongoDB `FeatureFlagAudit`.
- `FeatureFlagReconciler` periodically resyncs the local cache from Redis every `FEATURE_FLAG_RECONCILE_MS` (minimum `5000ms`).
- If Redis is unreachable, evaluation degrades gracefully to the last known in-memory snapshot or default `False` without failing user requests.

Acceptance criteria:
- Updating a flag on one instance invalidates the in-memory cache on peer instances via Redis Pub/Sub within milliseconds and records a MongoDB `FeatureFlagAudit` document.

### REQ-016: Fail-Open Notifications, Conformance Harness & 8-Tier Verification Suite
Experience: none
Given: `NotificationModule`, `ConformanceModule`, and `scripts/verify.sh`
When: Notifications are dispatched, conformance tests run, or `scripts/verify.sh` executes
Then:
- `NotificationService.send()` posts formatted alerts to `SLACK_WEBHOOK_URL` (no-op if unset) and catches/logs all network errors so notification failures never fail caller transactions.
- When `CONFORMANCE_HARNESS_ENABLED=true` (and `NODE_ENV != "production"`), mounts `/conformance/cache`, `/conformance/deadline`, `/conformance/http-client` (enforcing loopback-only targets via `assert_loopback_target_url`), `/conformance/priority`, `/conformance/rate-limit`, and `/conformance/redaction`.
- `scripts/verify.sh` runs all 8 quality tiers (Ruff lint/format, Mypy strict type-checking, Pytest unit tests, Testcontainers integration tests, Hypothesis property tests, GraphQL/REST contract tests, Mutmut incremental mutation tests, and Semgrep/pip-audit/OSV/Gitleaks security scans).

Acceptance criteria:
- `scripts/verify.sh` exits `0` with all 8 quality gates passing.

## Design
See [docs/adr/0001-stack.md](file:///Users/pc/Documents/personal/fastapi_backend_boilerplate/docs/adr/0001-stack.md) and [docs/architecture.md](file:///Users/pc/Documents/personal/fastapi_backend_boilerplate/docs/architecture.md) for the full module topology and library mapping.

## Policy check
Skills applied: `plan`, `project-brief`

## Flagged concerns
- Tier 2 (HIGH) scope includes authentication, cryptography, database migrations, and security perimeters — requires human acceptance of `intent/fastapi-boilerplate/architecture-gate.md` before implementation begins.

## Open questions carried forward
None.

## Risk tier
Tier: 2 (HIGH)
Class: HIGH
Why: Introduces authentication (RS256 JWT, Argon2id, CSRF), database schemas/migrations (PostgreSQL & MongoDB), PII redaction, file upload virus/magic-byte scanning, and public REST/GraphQL/WebSocket API contracts.

## Non-functional impact
Documented in [docs/nfr.md](file:///Users/pc/Documents/personal/fastapi_backend_boilerplate/docs/nfr.md).

## Abuse and failure cases
Documented in [docs/threat-model.md](file:///Users/pc/Documents/personal/fastapi_backend_boilerplate/docs/threat-model.md).

## Data impact
- **PostgreSQL migrations:** `migrations/postgres/001_create_country_access.sql` (`country_access_rules`) and `migrations/postgres/002_create_background_jobs.sql` (`background_jobs` table + `background_job_status` enum + partial unique deduplication index).
- **MongoDB collections:** `users` (contains PII: `email`, `password_hash`), `categories`, and `feature_flag_audits` (contains admin `actor_email`).
- **Redis ephemeral keys:** `revoked_jti:*`, `session:*`, `ratelimit:*`, `cache:*`, `gql:*`, `ws:*`, `ff:*`.
