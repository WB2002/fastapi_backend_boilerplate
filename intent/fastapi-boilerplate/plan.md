# Plan: FastAPI Enterprise Backend Boilerplate (NestJS Parity)
Source: intent/fastapi-boilerplate/spec.md
Experience: none
Lifecycle: DRAFT

## Files that change
- **Project Root & Tooling:**
  - `pyproject.toml`, `uv.lock`, `.env.example`, `docker-compose.yml`, `.pre-commit-config.yaml`, `AGENTS.md`, `CHANGELOG.md`
- **Database Migrations:**
  - `migrations/postgres/001_create_country_access.sql`, `migrations/postgres/001_create_country_access.down.sql`
  - `migrations/postgres/002_create_background_jobs.sql`, `migrations/postgres/002_create_background_jobs.down.sql`
- **Application Entry & Core Config (`app/`):**
  - `app/__init__.py`, `app/main.py`, `app/instrumentation.py`, `app/app_controller.py`, `app/app_service.py`
  - `app/types/__init__.py`, `app/types/app_types.py`, `app/types/jwt_types.py`, `app/types/logger_types.py`
  - `app/common/config/__init__.py`, `app/common/config/env_schema.py`, `app/common/config/validate_env.py`, `app/common/config/settings.py`, `app/common/config/resolve_coordination_storage.py`, `app/common/config/parse_cache_module_options.py`, `app/common/config/parse_http_server_timeouts.py`
  - `app/common/constants/__init__.py`, `app/common/decorators/__init__.py`, `app/common/utils/__init__.py`
- **Observability, Logging, Redaction & Global Exception Filter:**
  - `app/core/redaction/__init__.py`, `app/core/redaction/redactor.py`
  - `app/core/logger/__init__.py`, `app/core/logger/logger_config.py`, `app/core/logger/logger_context.py`, `app/core/logger/logger_middleware.py`, `app/core/logger/tracing_interceptor.py`
  - `app/core/telemetry/__init__.py`, `app/core/telemetry/emf_metrics.py`, `app/core/telemetry/otel.py`
  - `app/common/filters/__init__.py`, `app/common/filters/all_exceptions_filter.py`
- **Server Lifecycle, Deadlines, Compression & Input Sanitization:**
  - `app/core/shutdown/__init__.py`, `app/core/shutdown/shutdown_state.py`, `app/core/shutdown/graceful_shutdown.py`
  - `app/core/request_deadline/__init__.py`, `app/core/request_deadline/deadline_context.py`, `app/core/request_deadline/deadline_middleware.py`
  - `app/core/compression/__init__.py`, `app/core/compression/compression_middleware.py`
  - `app/common/sanitization/__init__.py`, `app/common/sanitization/sanitization_pipe.py`
- **Polyglot Persistence & Health Checks:**
  - `app/common/redis/__init__.py`, `app/common/redis/redis_service.py`
  - `app/common/postgres/__init__.py`, `app/common/postgres/postgres_pool.py`, `app/common/postgres/migrator.py`
  - `app/common/mongo/__init__.py`, `app/common/mongo/mongo_service.py`
  - `app/core/health/__init__.py`, `app/core/health/health_snapshot.py`, `app/core/health/health_controller.py`
- **Authentication, Argon2id Bulkhead & Double-Submit CSRF:**
  - `app/core/csrf/__init__.py`, `app/core/csrf/csrf_token_service.py`, `app/core/csrf/csrf_middleware.py`
  - `app/auth/__init__.py`, `app/auth/schemas/user_schema.py`, `app/auth/dto/auth_dto.py`, `app/auth/password/password_hash_bulkhead.py`, `app/auth/access_token_authenticator.py`, `app/auth/guards/jwt_auth_guard.py`, `app/auth/guards/roles_guard.py`, `app/auth/auth_service.py`, `app/auth/auth_controller.py`
- **Inbound Overload, Priority Shedding & Rate Limiting:**
  - `app/core/overload/__init__.py`, `app/core/overload/under_pressure.py`, `app/core/overload/priority_bulkhead_guard.py`, `app/core/overload/load_leveling_queue.py`, `app/core/overload/overload_telemetry.py`, `app/core/overload/errors.py`
  - `app/core/rate_limiter/__init__.py`, `app/core/rate_limiter/guest_id_middleware.py`, `app/core/rate_limiter/stores.py`, `app/core/rate_limiter/rate_limit_guard.py`
- **Resilient Outbound HTTP Client & Multi-Tier SWR Cache:**
  - `app/core/http_client/__init__.py`, `app/core/http_client/ssrf_validator.py`, `app/core/http_client/retry_policy.py`, `app/core/http_client/circuit_breaker.py`, `app/core/http_client/concurrency_limiter.py`, `app/core/http_client/single_flight.py`, `app/core/http_client/http_client_factory.py`
  - `app/core/cache/__init__.py`, `app/core/cache/cache_stores.py`, `app/core/cache/stampede_lock.py`, `app/core/cache/cache_service.py`
- **GeoIP Country Access, File Uploads (Magic-Byte + ClamAV), hCaptcha & Slack Notifications:**
  - `app/common/country_access/__init__.py`, `app/common/country_access/trust_proxy.py`, `app/common/country_access/geoip_reader.py`, `app/common/country_access/postgres_country_repository.py`, `app/common/country_access/country_access_guard.py`
  - `app/common/uploads/__init__.py`, `app/common/uploads/magic_byte_validator.py`, `app/common/uploads/clamav_scanner.py`, `app/common/uploads/uploads_service.py`, `app/common/uploads/uploads_controller.py`
  - `app/core/captcha/__init__.py`, `app/core/captcha/captcha_service.py`
  - `app/core/notification/__init__.py`, `app/core/notification/slack_notification_service.py`
- **Dynamic Feature Flags:**
  - `app/core/feature_flag/__init__.py`, `app/core/feature_flag/schemas.py`, `app/core/feature_flag/feature_flag_repository.py`, `app/core/feature_flag/feature_flag_service.py`, `app/core/feature_flag/feature_flag_subscriber.py`, `app/core/feature_flag/feature_flag_reconciler.py`, `app/core/feature_flag/feature_flag_audit_service.py`, `app/core/feature_flag/feature_flag_health.py`, `app/core/feature_flag/feature_flag_controller.py`
- **Background Jobs Engine (Celery + Redis + PostgreSQL Ledger):**
  - `app/core/background_jobs/__init__.py`, `app/core/background_jobs/postgres_job_repository.py`, `app/core/background_jobs/celery_app.py`, `app/core/background_jobs/job_handler_registry.py`, `app/core/background_jobs/job_enqueue_service.py`, `app/core/background_jobs/lease_reclaim_service.py`, `app/core/background_jobs/worker_processor.py`
- **Real-Time WebSockets (`python-socketio` + Redis Adapter):**
  - `app/core/websocket/__init__.py`, `app/core/websocket/config.py`, `app/core/websocket/ws_handshake_service.py`, `app/core/websocket/ws_connection_limiter.py`, `app/core/websocket/ws_message_rate_limiter.py`, `app/core/websocket/redis_socketio_server.py`, `app/core/websocket/websocket_shutdown_service.py`
- **GraphQL API (Strawberry + Redis Complexity/Concurrency Extension):**
  - `app/categories/__init__.py`, `app/categories/schemas.py`, `app/categories/categories_service.py`, `app/categories/categories_graphql.py`
  - `app/common/plugins/__init__.py`, `app/common/plugins/graphql_concurrency.py`, `app/common/plugins/complexity_extension.py`
- **Example & Conformance Harness Modules:**
  - `app/example/__init__.py`, `app/example/example_captcha_controller.py`, `app/example/example_notification_controller.py`, `app/example/example_jobs_controller.py`, `app/example/example_realtime_gateway.py`
  - `app/conformance/__init__.py`, `app/conformance/assert_loopback_target_url.py`, `app/conformance/conformance_cache_controller.py`, `app/conformance/conformance_deadline_controller.py`, `app/conformance/conformance_http_client_controller.py`, `app/conformance/conformance_priority_controller.py`, `app/conformance/conformance_rate_limit_controller.py`, `app/conformance/conformance_redaction_controller.py`
- **Testing & CI Quality Scripts:**
  - `tests/conftest.py`, `tests/testing/integration_containers.py`
  - `tests/unit/**`, `tests/integration/**`, `tests/property/**`, `tests/contracts/graphql/schema.snapshot.graphql`, `tests/contracts/test_graphql_contract.py`
  - `scripts/verify.sh`, `scripts/verify-full.sh`, `scripts/smoke-graceful-shutdown.sh`, `scripts/gitleaks-pre-commit.sh`, `scripts/ci/run-mutation.sh`, `scripts/ci/run-semgrep.sh`, `scripts/ci/run-osv-scanner.sh`, `scripts/ci/run-pip-audit.sh`, `scripts/ci/scan-long-base64-literals.sh`

## Order of work
The 18 tasks are ordered so every layer builds on tested lower-level primitives without circular dependencies:
1. **Foundation & Runtime:** `TASK-001` (Project Bootstrap & `uv` Toolchain) -> `TASK-002` (67-Env Schema Validator) -> `TASK-003` (Logging, PII Redaction, OTel X-Ray & EMF Telemetry) -> `TASK-004` (Core ASGI Runtime, Helmet CSP, Compression, Cookies, Deadlines, `AllExceptionsFilter` & Graceful Shutdown).
2. **Persistence & Health:** `TASK-005` (Polyglot Persistence: Postgres Async + SQL Migrations, Lazy Mongo/Beanie, Redis Service) -> `TASK-006` (Terminus Health Probes & `HealthSnapshot` Cache).
3. **Security & Traffic Control:** `TASK-007` (HTML Input Sanitization Pipe & Double-Submit HMAC CSRF) -> `TASK-008` (Auth Module: RS256 JWT, Argon2id Bulkhead, Redis JTI Revocation & Sessions) -> `TASK-009` (Inbound Overload: Event-Loop/Memory Under-Pressure, Priority Shedding & Load Leveling) -> `TASK-010` (Rate Limiter: Memory/Redis Cooldown Failover & Multi-Identifier Guards).
4. **Resilience & Caching:** `TASK-011` (Resilient Outbound HTTP Client with SSRF Defense, Retries, Circuit Breaker, AIMD Concurrency & Single-Flight) -> `TASK-012` (Multi-Tier SWR Cache & Distributed Redis Stampede Protection).
5. **Edge Security & Operational Services:** `TASK-013` (GeoIP Country Access Guard, Multipart Uploads with Magic-Byte + ClamAV, hCaptcha & Slack Notifications) -> `TASK-014` (Dynamic Feature Flags with Redis Pub/Sub, Reconciler & MongoDB Audit Log).
6. **Async Processing, Real-Time & GraphQL:** `TASK-015` (Transactional Background Jobs: Celery + Redis Broker + PostgreSQL ACID Ledger & Lease Reclaim) -> `TASK-016` (Real-Time WebSockets: `python-socketio` + Redis Adapter, Handshake Auth, Connection/Message Limits & `ExampleRealtimeGateway`) -> `TASK-017` (Strawberry GraphQL `/graphql` + `Categories` + Redis Complexity/Budget/Concurrency Extension).
7. **Conformance & 8-Tier Verification:** `TASK-018` (Conformance Harness `/conformance/*`, Testcontainers Suite, Property/Contract/Mutation/Security Scripts & `scripts/verify.sh`).

---

## Tasks

### TASK-001: Bootstrap Python 3.12+ / `uv` Project & Quality Toolchain
Goal: Initialize the Python package manifest (`pyproject.toml`), dependency lockfile (`uv.lock`), Docker Compose local backing services, and base `app/` package layout.
Requirements: REQ-016
Experience: none
States covered: N/A
Files:
- `pyproject.toml`, `.env.example`, `docker-compose.yml`, `.gitignore`, `AGENTS.md`
- `app/__init__.py`, `app/types/__init__.py`, `app/types/app_types.py`, `app/types/jwt_types.py`, `app/types/logger_types.py`
Dependencies: `docs/adr/0001-stack.md` accepted.
Acceptance criteria:
- `uv sync --frozen` installs all runtime and dev dependencies (`fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `motor`, `beanie`, `redis`, `pyjwt[cryptography]`, `argon2-cffi`, `itsdangerous`, `brotli-asgi`, `nh3`, `httpx`, `celery[redis]`, `python-socketio`, `strawberry-graphql[fastapi]`, `geoip2`, `filetype`, `structlog`, `psutil`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-sdk-extension-aws`, `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `testcontainers`, `hypothesis`, `mutmut`, `pip-audit`).
- `uv run ruff check .` and `uv run mypy app tests` execute cleanly.
Tests:
- `tests/unit/test_bootstrap.py`
Risks:
- Native C/Rust wheel compatibility on macOS/Linux (mitigated by standard prebuilt wheels for `asyncpg`, `argon2-cffi`, `nh3`, `cryptography`).

### TASK-002: Declarative 67-Variable Environment Schema & Fail-Fast Validator
Goal: Port `env.schema.ts` and `validate-env.ts` to `app/common/config/` so startup validates all 67 environment keys, reports *all* missing required keys at once, and forbids `CONFORMANCE_HARNESS_ENABLED=true` in production.
Requirements: REQ-001
Experience: none
States covered: Boot validation success, multi-key missing failure, production conformance guard failure.
Files:
- `app/common/config/env_schema.py`
- `app/common/config/validate_env.py`
- `app/common/config/settings.py`
- `app/common/config/resolve_coordination_storage.py`
- `app/common/config/parse_cache_module_options.py`
- `app/common/config/parse_http_server_timeouts.py`
Dependencies: TASK-001
Acceptance criteria:
- Calling `validate_env(config)` with 3 missing required keys raises `ValueError("Missing required environment variables: <key1>, <key2>, <key3>")` listing every missing key.
- Calling `validate_env({"NODE_ENV": "production", "CONFORMANCE_HARNESS_ENABLED": "true", ...})` raises `ValueError("CONFORMANCE_HARNESS_ENABLED cannot be true when NODE_ENV=production")`.
- Storage resolvers default `RATE_LIMIT_STORAGE` and `CACHE_STORAGE` to `memory` in development/test and `redis` in production when unset.
Tests:
- `tests/unit/common/config/test_validate_env.py`
- `tests/unit/common/config/test_resolve_coordination_storage.py`
- `tests/property/test_config_parsers_property.py`
Risks:
- Divergence in env variable names or defaults from NestJS (mitigated by 1:1 table test against all 67 keys in `env.schema.ts`).

### TASK-003: Structured Logger, Recursive PII Redaction, OpenTelemetry X-Ray & CloudWatch EMF
Goal: Implement `structlog` JSON logging with `X-Request-Id` context propagation, recursive PII redaction (`LOG_REDACTION_ENABLED`), OpenTelemetry SDK with AWS X-Ray ID generator/propagator (`OTEL_ENABLED`), and CloudWatch EMF metric emission (`APP_METRICS_ENABLED`).
Requirements: REQ-014
Experience: none
States covered: Redaction enabled/disabled, OTel enabled/disabled, EMF metric emission on 503/overload/feature-flags.
Files:
- `app/core/redaction/redactor.py`
- `app/core/logger/logger_config.py`, `app/core/logger/logger_context.py`, `app/core/logger/logger_middleware.py`, `app/core/logger/tracing_interceptor.py`
- `app/core/telemetry/emf_metrics.py`, `app/core/telemetry/otel.py`
- `app/instrumentation.py`
Dependencies: TASK-002
Acceptance criteria:
- `redact_sensitive_data(payload)` masks sensitive keys (`password`, `token`, `secret`, `authorization`, `cookie`, `accessToken`, `sessionId`, `csrfToken`, `creditCard`, `ssn`) and inline regexes (emails, JWTs, Bearer tokens) across nested dicts, lists, and strings without mutating caller objects or failing on circular references.
- `LoggerMiddleware` extracts or generates `X-Request-Id`, binds it to `ContextVar`, and writes `X-Request-Id` on the response.
- EMF helper emits valid CloudWatch `_aws` JSON metric blobs for `InboundBulkhead503`, `ApiPriorityShed503`, `InboundLoadLevelQueueFull503`, `InboundLoadLevelWaitTimeout503`, `UnderPressure503`, `DependencyUnavailable503`, `HealthCheck503`, `GraphqlConcurrencyDenied`, and `FeatureFlagEvaluations`.
Tests:
- `tests/unit/core/redaction/test_redactor.py`
- `tests/unit/core/logger/test_logger_middleware.py`
- `tests/unit/core/telemetry/test_emf_metrics.py`
- `tests/property/test_redaction_property.py`
Risks:
- Deep recursion or circular references in logged objects causing `RecursionError` (mitigated by depth cap and seen-object tracking in `redactor.py`).

### TASK-004: Core ASGI Runtime, Helmet Security Headers, Compression, Cookies, Deadlines, `AllExceptionsFilter` & Graceful Shutdown
Goal: Build the FastAPI application factory (`app/main.py`) with Helmet CSP headers, Brotli/Gzip compression (`COMPRESSION_THRESHOLD`), HMAC-signed cookies (`COOKIE_SECRET`), `TRUST_PROXY` client IP resolution, request deadline propagation (`504`/`499`), `AllExceptionsFilter`, and two-phase `SIGTERM` graceful shutdown (`SHUTDOWN_DRAIN_MS`, `SHUTDOWN_FORCE_MS`).
Requirements: REQ-002, REQ-003
Experience: none
States covered: Normal request, compressed response, deadline timeout (504), client disconnect (499), DB connection failure (503), Terminus health failure (503), SIGTERM draining (503 on readiness) -> shutdown.
Files:
- `app/core/shutdown/shutdown_state.py`, `app/core/shutdown/graceful_shutdown.py`
- `app/core/request_deadline/deadline_context.py`, `app/core/request_deadline/deadline_middleware.py`
- `app/core/compression/compression_middleware.py`
- `app/common/filters/all_exceptions_filter.py`
- `app/app_controller.py`, `app/app_service.py`, `app/main.py`
Dependencies: TASK-003
Acceptance criteria:
- `RequestDeadlineMiddleware` computes `effective_timeout = min(route_or_header_timeout, REQUEST_TIMEOUT_MAX_MS)`. When exceeded, cancels handler and returns HTTP `504` `{ statusCode: 504, timestamp, path, requestId, message: "Request deadline exceeded" }`. When client disconnects early, cancels handler and logs/maps to `499` (`"Request cancelled by client"`).
- `AllExceptionsFilter` formats all HTTP and uncaught exceptions into `{ statusCode, timestamp, path, requestId, message }`, preserving Terminus `{ status, info, error, details }` top-level keys on health 503 responses and setting `Retry-After` on all overload 503s.
- Compression middleware compresses payloads `>= COMPRESSION_THRESHOLD` (`1024` bytes) with `br` or `gzip` based on `Accept-Encoding`.
- `register_graceful_shutdown` intercepts `SIGTERM`/`SIGINT`, sets `ShutdownState.is_shutting_down = True`, sleeps `SHUTDOWN_DRAIN_MS` (`10000ms`), executes `before_close` hooks (WebSocket close, Mongo/Postgres/Redis teardown) within `SHUTDOWN_FORCE_MS` (`5000ms`).
Tests:
- `tests/unit/core/request_deadline/test_deadline_middleware.py`
- `tests/unit/common/filters/test_all_exceptions_filter.py`
- `tests/unit/core/shutdown/test_graceful_shutdown.py`
- `tests/unit/core/compression/test_compression_middleware.py`
Risks:
- Starlette `BaseHTTPMiddleware` task wrapping caveats with `ContextVar` and streaming responses (mitigated by implementing raw ASGI middleware classes for `LoggerMiddleware`, `RequestDeadlineMiddleware`, `CsrfMiddleware`, and `GuestIdMiddleware`).

### TASK-005: Polyglot Persistence Layer (PostgreSQL Async + SQL Migrations, Lazy MongoDB/Beanie, Redis Service)
Goal: Implement `PostgresPool` (`asyncpg` / SQLAlchemy 2.0 Async) with SQL migration runner (`migrations/postgres/001_*.sql`, `002_*.sql`), `MongoService` (Motor + Beanie with lazy connection and `2000ms` fail-fast timeouts), and `RedisService` (`redis.asyncio` with `.duplicate()` for Pub/Sub).
Requirements: REQ-005
Experience: none
States covered: All stores healthy, MongoDB unreachable at boot (lazy non-blocking startup), Postgres/Redis reconnects, clean teardown during shutdown.
Files:
- `migrations/postgres/001_create_country_access.sql`, `migrations/postgres/001_create_country_access.down.sql`
- `migrations/postgres/002_create_background_jobs.sql`, `migrations/postgres/002_create_background_jobs.down.sql`
- `app/common/postgres/postgres_pool.py`, `app/common/postgres/migrator.py`
- `app/common/mongo/mongo_service.py`
- `app/common/redis/redis_service.py`
Dependencies: TASK-004
Acceptance criteria:
- `migrations/postgres/001_create_country_access.sql` and `002_create_background_jobs.sql` match the NestJS schemas byte-for-byte.
- App boots without throwing or hanging when `MONGO_URI` points to an unreachable port (`serverSelectionTimeoutMS=2000`, `connectTimeoutMS=2000`).
- `RedisService.duplicate()` returns an isolated `redis.asyncio.Redis` connection suitable for Pub/Sub listeners.
Tests:
- `tests/unit/common/redis/test_redis_service.py`
- `tests/unit/common/mongo/test_mongo_lazy_boot.py`
- `tests/integration/persistence/test_polyglot_persistence_integration.py`
Risks:
- Beanie `init_beanie` attempting synchronous collection index creation at startup when MongoDB is down (mitigated by background/lazy index initialization with a 2s timeout catch so startup never blocks).

### TASK-006: Terminus-Compatible Health Probes (`/health`, `/health/ready`, `/health/live`) & `HealthSnapshot`
Goal: Port `HealthController` and `HealthSnapshot` to `/health`, `/health/ready`, and `/health/live` with `HEALTH_CHECK_CACHE_TTL_MS=2000` single-flight response caching and bypass decorators (`@public`, `@skip_rate_limit`, `@skip_request_deadline`, `@skip_country_access`, `@skip_inbound_bulkhead`, `@skip_tracing`).
Requirements: REQ-014
Experience: none
States covered: Healthy (`200`), dependency down (`503` with Terminus details), shutting down (`503` on `/health/live` and `/health/ready`), cached burst (`HealthSnapshot`).
Files:
- `app/core/health/health_snapshot.py`
- `app/core/health/health_controller.py`
Dependencies: TASK-005
Acceptance criteria:
- `GET /health/live` returns `200 { "status": "ok" }` normally and `503` when `shutdown_state.is_shutting_down` is `True`.
- `GET /health` and `GET /health/ready` check `mongo`, `memory_heap` (`150MB` threshold), `storage` (`90%` threshold on `/`), `feature_flags` (informational, never fails readiness), `redis` (when `REDIS_HOST` set, 3s timeout), and `postgres` (`SELECT 1` when `POSTGRES_DB` set, 2s timeout).
- When any non-informational check fails, returns HTTP `503` with `{ statusCode: 503, timestamp, path, requestId, status: "error", info: {...}, error: {...}, details: {...} }` and records `HealthCheck503`.
Tests:
- `tests/unit/core/health/test_health_snapshot.py`
- `tests/unit/core/health/test_health_controller.py`
Risks:
- Health probe storms from load balancers overwhelming databases (mitigated by `HealthSnapshot` single-flight + 2000ms TTL cache).

### TASK-007: Pre-Validation HTML Sanitization Pipe & Double-Submit HMAC CSRF Middleware
Goal: Implement recursive HTML input sanitization (`nh3`) prior to Pydantic model validation (`SanitizationPipe`) and double-submit HMAC-SHA256 CSRF protection (`CsrfMiddleware`, `CsrfTokenService`, `/auth/csrf`).
Requirements: REQ-004, REQ-013
Experience: none
States covered: Clean input, XSS-laden input stripped before length validation, safe HTTP methods allowed (`GET`, `HEAD`, `OPTIONS`), exempt paths bypassed (`CSRF_EXEMPT_PATHS`), valid CSRF token + cookie accepted, missing/mismatched/tampered CSRF token rejected (`403`).
Files:
- `app/common/sanitization/sanitization_pipe.py`
- `app/core/csrf/csrf_token_service.py`, `app/core/csrf/csrf_middleware.py`
Dependencies: TASK-004
Acceptance criteria:
- Strings containing `<script>alert(1)</script>hello` are sanitized to `"hello"` before Pydantic `min_length`/`max_length` constraints run, so a payload of `<script>...</script>a` fails `min_length=3` after tag stripping.
- `CsrfTokenService` generates and verifies HMAC-SHA256 signed tokens using `COOKIE_SECRET` with constant-time comparison (` hmac.compare_digest`).
- `CsrfMiddleware` rejects non-exempt `POST`/`PUT`/`PATCH`/`DELETE` requests with HTTP `403` when `CSRF_ENABLED=true` and `csrf_token` cookie or `X-CSRF-Token` header is missing, mismatched, or invalidly signed.
Tests:
- `tests/unit/common/sanitization/test_sanitization_pipe.py`
- `tests/unit/core/csrf/test_csrf_middleware.py`
- `tests/property/test_csrf_property.py`
Risks:
- Body consumption in ASGI middleware breaking downstream FastAPI route body parsing (mitigated by verifying CSRF via header + cookie without consuming request body stream, and applying `SanitizationPipe` via Pydantic root `@model_validator(mode="before")` / dependency base model).

### TASK-008: Authentication & Authorization (RS256 JWT, Argon2id Bulkhead, Redis JTI Revocation, Sessions & RBAC)
Goal: Implement `AuthModule` (`/auth/csrf`, `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`), MongoDB `User` document, Argon2id password hashing with concurrency bulkhead, RS256 JWT issuance/verification, Redis `revoked_jti:*` & session store, global `JwtAuthGuard` (`@public` opt-out), and `@require_roles` guard.
Requirements: REQ-004
Experience: none
States covered: Registration, login cookie + JSON token issuance, authenticated request (cookie or Bearer), token refresh rotation, logout JTI revocation, Argon2id bulkhead queue saturation (`503`), unauthorized/expired/revoked token (`401`), insufficient role (`403`).
Files:
- `app/auth/schemas/user_schema.py`, `app/auth/dto/auth_dto.py`
- `app/auth/password/password_hash_bulkhead.py`
- `app/auth/access_token_authenticator.py`
- `app/auth/guards/jwt_auth_guard.py`, `app/auth/guards/roles_guard.py`
- `app/auth/auth_service.py`, `app/auth/auth_controller.py`
Dependencies: TASK-005, TASK-007
Acceptance criteria:
- `PasswordHashBulkhead` limits active Argon2id threads (`asyncio.to_thread`) to `AUTH_PASSWORD_HASH_MAX_CONCURRENT=2` with queue limit `AUTH_PASSWORD_HASH_MAX_QUEUE=32` and wait timeout `AUTH_PASSWORD_HASH_MAX_WAIT_MS=2000ms`; overflow or timeout raises `503`.
- `/auth/controller` is decorated with `@api_priority(ApiPriorityLevel.CRITICAL)`.
- `/auth/login` and `/auth/refresh` set `access_token` (`HttpOnly`, `path=/`, `maxAge=ACCESS_TOKEN_TTL`), `session_id` (`HttpOnly`, `path=/auth/refresh`, `maxAge=SESSION_TTL`), and `csrf_token` cookies, and return `{ accessToken, sessionId, csrfToken }`.
- `/auth/logout` marks the session/JTI revoked in Redis (`revoked_jti:<id>`) and clears `access_token` and `session_id` cookies.
Tests:
- `tests/unit/auth/test_password_hash_bulkhead.py`
- `tests/unit/auth/test_auth_service.py`
- `tests/unit/auth/test_jwt_auth_guard.py`
- `tests/integration/auth/test_auth_flow_integration.py`
Risks:
- RSA PEM key newline formatting in environment variables (mitigated by normalizing escaped `\\n` to `\n` when loading `JWT_PRIVATE_KEY` and `JWT_PUBLIC_KEY`).

### TASK-009: Inbound Overload Protection, Event-Loop Under-Pressure, Priority Shedding & HTTP Load Leveling
Goal: Port `OverloadModule` (`under-pressure` event-loop lag & RSS/heap monitor, per-VM concurrency bulkhead `INBOUND_MAX_IN_FLIGHT`, progressive `@api_priority` shedding across `low`/`normal`/`high`/`critical`, and bounded FIFO HTTP load-leveling wait queue) with `Retry-After` and CloudWatch EMF overload telemetry.
Requirements: REQ-007
Experience: none
States covered: Healthy load, event-loop lag or memory threshold exceeded (`503`), progressive priority shedding at utilization thresholds (`503` + `ApiPriorityShed503`), load-leveling queue wait -> acquire, load-leveling queue full (`503` + `InboundLoadLevelQueueFull503`), load-leveling wait timeout (`503` + `InboundLoadLevelWaitTimeout503`), bulkhead full (`503` + `InboundBulkhead503`).
Files:
- `app/core/overload/errors.py`
- `app/core/overload/under_pressure.py`
- `app/core/overload/priority_bulkhead_guard.py`
- `app/core/overload/load_leveling_queue.py`
- `app/core/overload/overload_telemetry.py`
Dependencies: TASK-003, TASK-004
Acceptance criteria:
- `should_shed_priority_request(utilization, priority, thresholds)` matches NestJS pure function behavior across all priority levels (`LOW`, `NORMAL`, `HIGH`, `CRITICAL`).
- All overload rejections return HTTP `503`, set `Retry-After: <OVERLOAD_RETRY_AFTER_SEC>`, and emit the corresponding EMF metric.
- When `UNDER_PRESSURE_EXPOSE_STATUS_ROUTE=true`, exposes `/status` reporting event loop delay and memory usage.
Tests:
- `tests/unit/core/overload/test_should_shed_priority_request.py`
- `tests/unit/core/overload/test_priority_bulkhead_guard.py`
- `tests/unit/core/overload/test_load_leveling_queue.py`
- `tests/unit/core/overload/test_under_pressure.py`
- `tests/property/test_overload_priority_property.py`
Risks:
- In-flight counter drift if an exception occurs mid-request (mitigated by strict `try ... finally` slot release in the guard/middleware).

### TASK-010: Pluggable Rate Limiter (`memory` / `redis` with Cooldown Failover) & Multi-Identifier Guards
Goal: Port `RateLimiterModule`, `GuestIdMiddleware`, `RateLimitGuard`, and memory/Redis fixed-window counter stores with automatic Redis-to-memory cooldown failover (`RATE_LIMIT_FALLBACK_COOLDOWN_MS=5000`) and identifiers (`Ip`, `UserId`, `DeviceFingerprint`, `GuestId`).
Requirements: REQ-006
Experience: none
States covered: Under limit, over limit (`429` + `Retry-After`), Redis healthy, Redis down -> automatic fallback to memory during cooldown -> automatic Redis recovery after cooldown expires, signed `guest_id` cookie issuance.
Files:
- `app/core/rate_limiter/guest_id_middleware.py`
- `app/core/rate_limiter/stores.py`
- `app/core/rate_limiter/rate_limit_guard.py`
Dependencies: TASK-005, TASK-008
Acceptance criteria:
- `GuestIdMiddleware` issues an HMAC-signed `guest_id` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` in production) when missing or tampered.
- `RateLimitGuard` runs *after* `JwtAuthGuard` so `RateLimitIdentifier.USER_ID` reads `req.state.user.id`.
- Global default policy (`global-ip`, `Ip`, `duration=60000`, `limit=10`, `cost=1`) and route-level `@rate_limit(...)` policies enforce limits and return `429` when exceeded.
- When Redis raises an error, `FallbackRateLimitStore` switches to `MemoryRateLimitStore` for `RATE_LIMIT_FALLBACK_COOLDOWN_MS` (`5000ms`) without failing the request.
Tests:
- `tests/unit/core/rate_limiter/test_guest_id_middleware.py`
- `tests/unit/core/rate_limiter/test_rate_limit_stores.py`
- `tests/unit/core/rate_limiter/test_rate_limit_guard.py`
- `tests/integration/rate_limiter/test_rate_limiter_redis_failover_integration.py`
Risks:
- Race conditions on Redis fixed-window counter TTL assignment (mitigated by atomic Lua script `INCRBY` + conditional `PEXPIRE`).

### TASK-011: Resilient Outbound HTTP Client (SSRF Defense, Retries with Full Jitter, Circuit Breaker, AIMD Concurrency & Single-Flight)
Goal: Port `HttpClientModule` and fluent `HttpClientFactory` wrapping `httpx.AsyncClient` with pre-connect DNS/IP SSRF blocking, single-flight GET coalescing, fixed or AIMD adaptive concurrency limiting (scaled by `HTTP_CLIENT_INSTANCE_COUNT`), sliding-window circuit breaker, exponential backoff with full jitter, and inbound request deadline propagation.
Requirements: REQ-008
Experience: none
States covered: External vs internal client, SSRF private IP blocked (`HTTP_CLIENT_ALLOW_PRIVATE_URLS=false`), transient 5xx/network retry with exponential full jitter, circuit breaker trip (`CLOSED` -> `OPEN` -> `HALF_OPEN` -> `CLOSED`), adaptive AIMD concurrency adjustment on 429/5xx/timeout, single-flight GET deduplication, caller deadline abort propagation.
Files:
- `app/core/http_client/ssrf_validator.py`
- `app/core/http_client/retry_policy.py`
- `app/core/http_client/circuit_breaker.py`
- `app/core/http_client/concurrency_limiter.py`
- `app/core/http_client/single_flight.py`
- `app/core/http_client/http_client_factory.py`
Dependencies: TASK-004
Acceptance criteria:
- `create_external()` blocks loopback (`127.0.0.0/8`, `::1`), RFC1918 private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`, `fe80::/10`), and carrier-grade NAT (`100.64.0.0/10`) unless `HTTP_CLIENT_ALLOW_PRIVATE_URLS=true`.
- Circuit breaker opens when failure rate reaches `failure_threshold` across minimum sample volume and rejects calls fast until `reset_timeout_ms` elapses.
- Adaptive concurrency limiter halves limit on backoff/timeout/5xx and additively increases on success within `[min_concurrency, max_concurrency / HTTP_CLIENT_INSTANCE_COUNT]`.
Tests:
- `tests/unit/core/http_client/test_ssrf_validator.py`
- `tests/unit/core/http_client/test_circuit_breaker.py`
- `tests/unit/core/http_client/test_concurrency_limiter.py`
- `tests/unit/core/http_client/test_http_client_pipeline.py`
- `tests/property/test_retry_jitter_property.py`
Risks:
- DNS rebinding between URL check and socket connect (mitigated by resolving IP in custom transport / pre-request hook and validating every resolved A/AAAA address).

### TASK-012: Multi-Tier Caching Layer (`memory` / `redis`) & Stampede Protection
Goal: Port `CacheModule` and `CacheService.get_or_set` implementing soft/hard TTL Stale-While-Revalidate (SWR), TTL jitter (`CACHE_TTL_JITTER_RATIO=0.15`), in-process single-flight future coalescing, distributed Redis loader lock (`SET NX PX` with `CACHE_LOADER_LOCK_TTL_MS=30000` and polling wait `CACHE_LOADER_WAIT_MS=5000`), and negative caching (`cache_null`).
Requirements: REQ-009
Experience: none
States covered: Fresh hit (`< soft_ttl`), stale hit (`soft_ttl <= age < hard_ttl` -> immediate return + async background revalidate), cold miss (`>= hard_ttl` -> single-flight + distributed Redis lock), negative cache hit (`null` sentinel with `CACHE_NEGATIVE_SOFT_TTL_MS` / `HARD_TTL_MS`), lock holder timeout fallback.
Files:
- `app/core/cache/cache_stores.py`
- `app/core/cache/stampede_lock.py`
- `app/core/cache/cache_service.py`
Dependencies: TASK-005
Acceptance criteria:
- Concurrent in-process callers for the same missing key share a single loader execution.
- Across multiple processes/instances sharing Redis, only the lock winner (`SET cache:lock:<key> <token> NX PX 30000`) executes the loader while peer readers poll/wait up to `CACHE_LOADER_WAIT_MS=5000ms` for the populated value; lock release uses an atomic Lua compare-and-delete script.
- Negative caching (`cache_null=True`) stores a distinguishably serialized null sentinel so `None` database results do not trigger repeated DB hits.
Tests:
- `tests/unit/core/cache/test_cache_service_swr.py`
- `tests/unit/core/cache/test_stampede_protection.py`
- `tests/integration/cache/test_redis_cache_stampede_integration.py`
- `tests/property/test_cache_ttl_jitter_property.py`
Risks:
- Background SWR refresh task exceptions going unobserved (mitigated by wrapping background refresh tasks with structured error logging and in-flight refresh deduplication).

### TASK-013: Edge Security & Services — GeoIP Country Access, Uploads (Magic-Byte + ClamAV), hCaptcha & Slack Notifications
Goal: Implement `CountryAccessModule` (MaxMind GeoLite2 `.mmdb` + Postgres rules cache), `UploadsModule` (streaming size caps, `filetype` magic-byte inspection, ClamAV TCP `zINSTREAM` scan), `CaptchaModule` (hCaptcha token verification), and fail-open `NotificationModule` (Slack webhook).
Requirements: REQ-013, REQ-016
Experience: none
States covered: Allowed country, blocked country (`403`), unknown IP country (`allow` vs `block` policy), valid multipart upload, spoofed MIME/extension upload rejected (`400`), oversized file/total payload rejected (`413`/`400`), ClamAV EICAR signature detected (`400`), hCaptcha valid/invalid token, Slack webhook success and fail-open on network error.
Files:
- `app/common/country_access/trust_proxy.py`, `app/common/country_access/geoip_reader.py`, `app/common/country_access/postgres_country_repository.py`, `app/common/country_access/country_access_guard.py`
- `app/common/uploads/magic_byte_validator.py`, `app/common/uploads/clamav_scanner.py`, `app/common/uploads/uploads_service.py`, `app/common/uploads/uploads_controller.py`
- `app/core/captcha/captcha_service.py`
- `app/core/notification/slack_notification_service.py`
- `app/example/example_captcha_controller.py`, `app/example/example_notification_controller.py`
Dependencies: TASK-005, TASK-011
Acceptance criteria:
- `CountryAccessGuard` runs after `JwtAuthGuard` and before `RateLimitGuard`, consulting MaxMind `.mmdb` (`GEOIP_DATABASE_PATH`) and cached Postgres `country_access_rules` (`COUNTRY_ACCESS_REFRESH_INTERVAL_MS`), respecting `COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY` (`block` default).
- `UploadsController` (`POST /uploads`) enforces `UPLOAD_MAX_FILES=5`, `UPLOAD_MAX_FILE_SIZE=5000000`, `UPLOAD_MAX_TOTAL_SIZE=20000000`, verifies magic bytes match declared MIME/extension, and scans via ClamAV TCP `zINSTREAM` when `UPLOAD_VIRUS_SCAN=true`.
- `SlackNotificationService.send()` uses the resilient HTTP client and catches all exceptions (fail-open).
Tests:
- `tests/unit/common/country_access/test_country_access_guard.py`
- `tests/unit/common/uploads/test_uploads_service.py`
- `tests/unit/core/captcha/test_captcha_service.py`
- `tests/unit/core/notification/test_slack_notification_service.py`
Risks:
- Missing `/data/GeoLite2-Country.mmdb` in local dev/test environments (mitigated by test fixture `.mmdb` generator / mockable reader adapter matching NestJS test setup).

### TASK-014: Dynamic Feature Flags (Redis Store, In-Memory Cache, Pub/Sub Invalidation, Background Reconciler & MongoDB Audit Log)
Goal: Port `FeatureFlagModule` supporting boolean, user allowlist, and deterministic percentage rollouts with in-memory evaluation cache, Redis Pub/Sub invalidation (`FeatureFlagSubscriber`), periodic background reconciliation (`FEATURE_FLAG_RECONCILE_MS=45000`), admin CRUD controller (`FEATURE_FLAG_ADMIN_EMAILS`), MongoDB `FeatureFlagAudit` logs, and EMF evaluation metrics (`FEATURE_FLAG_METRICS`).
Requirements: REQ-015
Experience: none
States covered: Flag evaluation (boolean, allowlist, percentage rollout), admin create/update/delete flag, non-admin forbidden (`403`), Redis Pub/Sub cross-instance cache update, periodic reconciler sync, Redis outage fail-open to local cache, MongoDB audit log creation.
Files:
- `app/core/feature_flag/schemas.py`
- `app/core/feature_flag/feature_flag_repository.py`
- `app/core/feature_flag/feature_flag_service.py`
- `app/core/feature_flag/feature_flag_subscriber.py`
- `app/core/feature_flag/feature_flag_reconciler.py`
- `app/core/feature_flag/feature_flag_audit_service.py`
- `app/core/feature_flag/feature_flag_health.py`
- `app/core/feature_flag/feature_flag_controller.py`
Dependencies: TASK-005, TASK-006, TASK-008
Acceptance criteria:
- `FeatureFlagService.is_enabled(flag_key, context)` evaluates from the local in-memory cache with zero blocking network calls on the hot path and emits EMF metric when `FEATURE_FLAG_METRICS=true`.
- Admin CRUD routes verify the caller's email is in `FEATURE_FLAG_ADMIN_EMAILS` (or role is `ADMIN`), write to Redis, publish invalidation on Redis Pub/Sub, and persist a `FeatureFlagAudit` document in MongoDB.
- `FeatureFlagReconciler` clamps interval to `>= 5000ms` (default `45000ms`) and refreshes local state from Redis.
Tests:
- `tests/unit/core/feature_flag/test_feature_flag_service.py`
- `tests/unit/core/feature_flag/test_feature_flag_controller.py`
- `tests/integration/feature_flag/test_feature_flag_pubsub_integration.py`
Risks:
- Pub/Sub subscriber task dying silently on temporary Redis disconnect (mitigated by auto-reconnecting subscriber loop plus the periodic `45000ms` reconciler safety net).

### TASK-015: Transactional Background Jobs Engine (Celery + Redis Broker + PostgreSQL ACID Ledger & Lease Reclaim)
Goal: Implement `BackgroundJobsModule` combining PostgreSQL `background_jobs` ACID ledger (`FOR UPDATE SKIP LOCKED` claims, active `dedupe_key` partial unique index, `lease_until` tracking), Celery Redis task producer/worker (`wake_job_row` with exponential backoff), `JobHandlerRegistry`, periodic orphan lease reclaim sweep (`BACKGROUND_JOBS_LEASE_RECLAIM_*`), and `ExampleJobsModule`.
Requirements: REQ-010
Experience: none
States covered: Enqueue new job, deduplicate active job with same `dedupe_key`, Celery wake task -> Postgres `FOR UPDATE SKIP LOCKED` claim -> handler execution -> mark `completed`, handler failure -> retry backoff / mark `failed`, crashed worker expired `lease_until` -> periodic reclaim sweep resets to `pending` and wakes Celery.
Files:
- `app/core/background_jobs/postgres_job_repository.py`
- `app/core/background_jobs/celery_app.py`
- `app/core/background_jobs/job_handler_registry.py`
- `app/core/background_jobs/job_enqueue_service.py`
- `app/core/background_jobs/lease_reclaim_service.py`
- `app/core/background_jobs/worker_processor.py`
- `app/example/example_jobs_controller.py`
Dependencies: TASK-005, TASK-008
Acceptance criteria:
- `JobEnqueueService.enqueue()` inserts into PostgreSQL `background_jobs` first; if `uq_background_jobs_dedupe_active` conflicts, returns `{ jobRowId: "", enqueued: false, deduplicated: true }`.
- Dispatches Celery task `background_jobs.run_job` with `task_id=job_row_id`, exponential backoff (`1s` base), and `max_retries=max_attempts`. If Redis dispatch fails, logs a warning and still returns `{ jobRowId, enqueued: true, deduplicated: false }` (the lease/orphan reclaimer will pick it up).
- Worker claims rows via `UPDATE background_jobs SET status = 'processing', locked_by = $1, locked_at = NOW(), lease_until = NOW() + $2::interval, attempts = attempts + 1 WHERE id = (SELECT id FROM background_jobs WHERE id = $3 AND status = 'pending' AND run_at <= NOW() FOR UPDATE SKIP LOCKED) RETURNING *`.
- `LeaseReclaimService` runs every `BACKGROUND_JOBS_LEASE_RECLAIM_INTERVAL_MS` (`30000ms`) when `BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED=true`, reclaiming stalled `processing` rows whose `lease_until < NOW()` and waking unscheduled `pending` orphans older than `BACKGROUND_JOBS_ORPHAN_MIN_AGE_SEC`.
Tests:
- `tests/unit/core/background_jobs/test_job_enqueue_service.py`
- `tests/unit/core/background_jobs/test_lease_reclaim_service.py`
- `tests/integration/background_jobs/test_background_jobs_postgres_celery_integration.py`
Risks:
- Celery sync worker running async job handlers (mitigated by using an event-loop runner bridge in the Celery task worker or running the worker claim loop inside the async worker lifecyle when `BACKGROUND_WORKER_ENABLED=true`).

### TASK-016: Real-Time WebSockets (`python-socketio` + Redis Adapter, Handshake Auth, Connection/Message Limits & `ExampleRealtimeGateway`)
Goal: Port `WebSocketModule` and `ExampleRealtimeGateway` using `python-socketio` (`AsyncServer` + `AsyncRedisManager`) mounted at `WEBSOCKET_PATH` (`/socket.io`) on namespace `/realtime`, enforcing production origin checks, JWT handshake auth, Redis per-user connection limits (`WEBSOCKET_MAX_CONNECTIONS_PER_USER=5`), per-socket message rate limiting (`120/min`), graceful shutdown disconnect, and events (`join`, `message`, `admin-broadcast`).
Requirements: REQ-011
Experience: none
States covered: Production wildcard origin boot rejection, authenticated handshake, unauthenticated/revoked JWT handshake disconnect, 6th concurrent connection for same user rejected, message rate limit exceeded, `join` own room (`user:<id>`) vs foreign room (`403`), `message` room emit with HTML sanitization, `admin-broadcast` RBAC check (`ADMIN` vs `USER`).
Files:
- `app/core/websocket/config.py`
- `app/core/websocket/ws_handshake_service.py`
- `app/core/websocket/ws_connection_limiter.py`
- `app/core/websocket/ws_message_rate_limiter.py`
- `app/core/websocket/redis_socketio_server.py`
- `app/core/websocket/websocket_shutdown_service.py`
- `app/example/example_realtime_gateway.py`
Dependencies: TASK-005, TASK-007, TASK-008
Acceptance criteria:
- `assert_websocket_production_origins` raises an error at boot if `WEBSOCKET_ENABLED=true`, `NODE_ENV=production`, and `ALLOWED_ORIGINS` contains `*`.
- Handshake verifies RS256 JWT (from `access_token` cookie, `Authorization: Bearer`, `auth.token`, or query `token` only if `WEBSOCKET_ALLOW_QUERY_TOKEN=true`) and Redis `revoked_jti:*`, then registers `client.sid` in a Redis Set/ZSet for `user.id` capped at `WEBSOCKET_MAX_CONNECTIONS_PER_USER=5` (TTL `86400s`).
- `WebSocketShutdownService.close_all()` disconnects all active sockets cleanly during `SIGTERM` `before_close`.
Tests:
- `tests/unit/core/websocket/test_ws_handshake_service.py`
- `tests/unit/core/websocket/test_ws_connection_limiter.py`
- `tests/unit/core/websocket/test_ws_message_rate_limiter.py`
- `tests/integration/websocket/test_socketio_realtime_integration.py`
Risks:
- Zombie connection entries in Redis if a node crashes before `disconnect` fires (mitigated by `WEBSOCKET_CONNECTION_KEY_TTL_SEC=86400` and stale SID pruning).

### TASK-017: GraphQL API (Strawberry `/graphql`, `CategoriesModule`, Depth Limit & Redis Complexity/Budget/Concurrency Extension)
Goal: Port `CategoriesModule` and `ComplexityPlugin` to Strawberry GraphQL mounted at `/graphql` with `QueryDepthLimiter(10)`, per-query complexity limit (`GQL_MAX_COMPLEXITY=50`), per-user/guest 60s Redis complexity budget (`GQL_USER_BUDGET=1000`), Redis concurrent execution lease (`GQL_MAX_CONCURRENCY=3`, TTL `300s`), OTel `graphql.operation.name` span attribute, and SDL contract snapshot test.
Requirements: REQ-012
Experience: none
States covered: Valid query (`categories`, `category`, `searchCategories`), depth > 10 rejected, complexity > 50 rejected (`QUERY_TOO_COMPLEX`), 60s budget > 1000 rejected (`OUT_OF_BUDGET`), concurrent executions > 3 rejected (`Retry-After` header + `GraphqlConcurrencyDenied` EMF metric), Redis degraded fallback.
Files:
- `app/categories/schemas.py`, `app/categories/categories_service.py`, `app/categories/categories_graphql.py`
- `app/common/plugins/graphql_concurrency.py`, `app/common/plugins/complexity_extension.py`
- `tests/contracts/graphql/schema.snapshot.graphql`
Dependencies: TASK-005, TASK-009, TASK-010
Acceptance criteria:
- Strawberry schema exports SDL matching NestJS `Category`, `CategoryFilterInput`, and `Query` (`categories`, `category(id: ID!)`, `searchCategories(filter: CategoryFilterInput!)`).
- `ComplexityExtension` acquires the Redis concurrency lease before execution and guarantees release on completion or error (`try ... finally`).
- Complexity calculation accounts for field multipliers/estimators and enforces both `GQL_MAX_COMPLEXITY` (`QUERY_TOO_COMPLEX`) and Redis 60s rolling budget `GQL_USER_BUDGET` (`OUT_OF_BUDGET`).
Tests:
- `tests/unit/common/plugins/test_complexity_extension.py`
- `tests/unit/categories/test_categories_resolver.py`
- `tests/contracts/test_graphql_contract.py`
Risks:
- Strawberry auto-camelCase vs exact SDL field names (verified by `tests/contracts/test_graphql_contract.py` comparing exported SDL against `schema.snapshot.graphql`).

### TASK-018: Conformance Harness (`/conformance/*`) & 8-Tier Verification Pipeline (`scripts/verify.sh`)
Goal: Port `ConformanceModule` (`/conformance/cache`, `/conformance/deadline`, `/conformance/http-client`, `/conformance/priority`, `/conformance/rate-limit`, `/conformance/redaction`) gated by `CONFORMANCE_HARNESS_ENABLED=true` (and refused in production), plus the complete 8-tier quality script suite (`scripts/verify.sh`, `scripts/verify-full.sh`, `scripts/smoke-graceful-shutdown.sh`, and `scripts/ci/*`).
Requirements: REQ-016
Experience: none
States covered: Conformance routes enabled in test/dev, disabled by default, refused in production; loopback-only SSRF enforcement on `/conformance/http-client`; all 8 verification tiers passing.
Files:
- `app/conformance/assert_loopback_target_url.py`
- `app/conformance/conformance_cache_controller.py`
- `app/conformance/conformance_deadline_controller.py`
- `app/conformance/conformance_http_client_controller.py`
- `app/conformance/conformance_priority_controller.py`
- `app/conformance/conformance_rate_limit_controller.py`
- `app/conformance/conformance_redaction_controller.py`
- `tests/testing/integration_containers.py`
- `scripts/verify.sh`, `scripts/verify-full.sh`, `scripts/smoke-graceful-shutdown.sh`, `scripts/gitleaks-pre-commit.sh`
- `scripts/ci/run-mutation.sh`, `scripts/ci/run-semgrep.sh`, `scripts/ci/run-osv-scanner.sh`, `scripts/ci/run-pip-audit.sh`, `scripts/ci/scan-long-base64-literals.sh`
Dependencies: TASK-001 through TASK-017
Acceptance criteria:
- `/conformance/http-client` validates target URL via `assert_loopback_target_url` (allowing only `127.0.0.1`, `localhost`, `[::1]`) so the test harness itself cannot be abused for external SSRF.
- `scripts/verify.sh` executes:
  1. `uv run ruff check . && uv run ruff format --check .` (Static Analysis & Formatting)
  2. `uv run mypy --strict app tests` (Strict Type Checking)
  3. `uv run pytest tests/unit` (Unit Tests)
  4. `uv run pytest tests/integration` (Testcontainers Integration Tests against Postgres, Mongo, Redis)
  5. `uv run pytest tests/property` (Hypothesis Property Tests)
  6. `uv run pytest tests/contracts` (GraphQL SDL & REST Contract Tests)
  7. `./scripts/ci/run-mutation.sh` (Incremental Mutmut Mutation Testing)
  8. `./scripts/ci/run-semgrep.sh && ./scripts/ci/scan-long-base64-literals.sh && ./scripts/ci/run-pip-audit.sh` (App Security & Supply Chain Scans)
Tests:
- `tests/unit/conformance/test_conformance_controllers.py`
- `tests/integration/conformance/test_conformance_e2e_integration.py`
Risks:
- Mutation testing runtime on large diffs (mitigated by scoping `run-mutation.sh` to changed files in `app/` via `git diff`, matching NestJS `scripts/ci/run-mutation.sh`).

---

## Plan Interrogation (Self-Critique)
1. **What could this break?**
   - **ASGI Middleware Ordering & ContextVars:** Starlette's `BaseHTTPMiddleware` runs downstream handlers in a separate `anyio` task group, which can swallow `ContextVar` mutations or interfere with streaming uploads and background cancellations. **Mitigation:** Implement all core middlewares (`LoggerMiddleware`, `GuestIdMiddleware`, `CsrfMiddleware`, `RequestDeadlineMiddleware`, `UnderPressureMiddleware`) as pure ASGI middleware classes (`async def __call__(self, scope, receive, send)`).
   - **Event-Loop Blocking:** Argon2id password hashing, synchronous MaxMind `.mmdb` reads, or ClamAV socket I/O could stall the asyncio event loop and falsely trip `UnderPressureMiddleware`. **Mitigation:** Run Argon2id inside `asyncio.to_thread` behind `PasswordHashBulkhead` (`max_concurrent=2`), use async TCP streams (`asyncio.open_connection`) for ClamAV `zINSTREAM`, and keep `.mmdb` database mapped in memory (`geoip2.database.Reader(..., mode=MODE_MEMORY)`).
2. **Which step is riskiest?**
   - **`TASK-015` (Transactional Background Jobs with Celery + PostgreSQL Ledger) & `TASK-005` (Lazy MongoDB Boot):** Coordinating async SQLAlchemy (`asyncpg`) inside Celery workers and ensuring Beanie/Motor does not block startup when MongoDB is offline. **Mitigation:** Lazy-initialize Beanie document indexes with a 2-second timeout catch so app boot always succeeds, and use a dedicated persistent asyncio event loop per Celery worker process for `FOR UPDATE SKIP LOCKED` claims and async job handlers.
3. **What alternatives did we reject?**
   - Consolidating MongoDB into PostgreSQL (rejected to preserve 1:1 polyglot parity with NestJS).
   - Replacing Socket.IO with raw WebSockets (rejected to preserve wire-level `/socket.io` and `/realtime` compatibility).
   - Replacing Celery with ARQ (rejected per explicit architectural decision to use Celery + Redis broker + PostgreSQL ACID ledger).

## Risks
- **Testcontainers Docker Daemon Availability:** Integration tests (`tests/integration/`) require a running Docker daemon. If Docker is unavailable in a restricted sandbox, unit/property/contract/type/lint checks run standalone while `verify-full.sh` runs the container suite.
- **Third-Party Binary Scanners (`semgrep`, `osv-scanner`, `gitleaks`, `trivy`):** CI scripts check for binary availability and use `uv run` / pinned tools so local verification is reproducible.

## Proof
- `uv run ruff check . && uv run ruff format --check .`
- `uv run mypy --strict app tests`
- `uv run pytest tests/unit -v`
- `uv run pytest tests/property -v`
- `uv run pytest tests/contracts -v`
- `uv run pytest tests/integration -v`
- `./scripts/smoke-graceful-shutdown.sh`
- `./scripts/verify.sh`

## Departures from plan
The following parity reconciliations against `.reference_nestjs/` ground truth supersede draft counts/names in `spec.md` and `plan.md`:
1. **103 Environment Variables (not 67):** `.reference_nestjs/src/common/config/env.schema.ts` declares 103 keys (10 `required: true`, 93 `required: false`). `app/common/config/env_schema.py` and `.env.example` implement all 103 keys in exact declaration order.
2. **Cookie Signing (`cookie-signature` format, `guest_id` only) & Random Base64url CSRF:** Only `guest_id` is HMAC-SHA256 signed using `@fastify/cookie` / `cookie-signature@1.2.2` format (`<uuid>.<base64_no_pad>`) via stdlib `hmac`/`hashlib`/`base64` rather than `itsdangerous` (retained in `pyproject.toml`); `access_token`, `session_id`, and `csrf_token` are unsigned. CSRF tokens are 32-byte random `base64url` compared via `hmac.compare_digest`, and `CsrfMiddleware` sends `{ statusCode: 403, message: "CSRF token invalid or missing", error: "Forbidden", code: "CSRF_VALIDATION_FAILED" }` directly (skipping Bearer auth and unauthenticated cookie-less requests).
3. **Auth Sessions & Guards:** Sessions live in MongoDB `Session` collection (TTL index on `expiresAt`); Redis stores `revoked_jti:<jti>`. No HTTP `RolesGuard` exists; role/permission guards are `WsRolesGuard` and `FeatureFlagPermissionsGuard` (`FEATURE_FLAG_ADMIN_EMAILS`).
4. **Outbound Deadline Header:** Propagation/response header is `x-request-deadline` (Unix epoch ms string), not `X-Request-Deadline-Ms`.
5. **CloudWatch EMF Schema:** Overload/503 metrics emit Namespace `'Application'`, Metric `'ServiceUnavailable'` (`Count`), Dimension `{ Reason }`; feature flags/HTTP client/country access emit under `'FeatureFlags'`, `'HttpClient'`, `'CountryAccess'`.
6. **Country Access Table:** Table is `country_access` (not `country_access_rules`); `CountryAccessService` starts `'inactive'` and reads `request.ip` via `TRUST_PROXY`.
7. **Notifications, Feature Flags, GraphQL, Conformance & Reference Exclusion:** `POST /example/notification` (201, fail-open, `https://hooks.slack.com`); feature flags stored as JSON strings `feature_flags:{env}:{key}` + index SET `feature_flags:index:{env}` (SHA-256 rollout, no per-user allowlist); GraphQL `CategoriesService` is in-memory 3-item seed and concurrency denial uses `TOO_MANY_CONCURRENT_REQUESTS` (`503` + `Retry-After` + `Cache-Control: no-store`); only `GET /conformance/rate-limit/user` is JWT-protected; `.reference_nestjs/` is excluded from all linters, typecheckers, tests, scanners, and packaging.
8. **Parser Defaults & Package Extras:** `python-multipart` is included in `pyproject.toml` runtime dependencies for FastAPI multipart uploads, and `pyjwt[crypto]` is used (PyJWT's actual extra name for `cryptography` support, instead of draft `pyjwt[cryptography]`). `OTEL_SERVICE_NAME` fallback remains `'nestjs-boilerplate'` and `country_access` `POSTGRES_DB` fallback remains `'nestjs_db'` for 100% `.reference_nestjs/` parser parity.

## Rollback
- All features are self-contained within this repository and guarded by environment toggles (`WEBSOCKET_ENABLED`, `BACKGROUND_JOBS_ENABLED`, `BACKGROUND_WORKER_ENABLED`, `UNDER_PRESSURE_ENABLED`, `INBOUND_BULKHEAD_ENABLED`, `INBOUND_PRIORITY_SHEDDING_ENABLED`, `INBOUND_LOAD_LEVELING_ENABLED`, `CSRF_ENABLED`, `UPLOAD_VIRUS_SCAN`, `OTEL_ENABLED`, `APP_METRICS_ENABLED`, `LOG_REDACTION_ENABLED`, `CONFORMANCE_HARNESS_ENABLED`).
- Database migrations include `.down.sql` rollback scripts (`migrations/postgres/001_create_country_access.down.sql`, `002_create_background_jobs.down.sql`). Rollback takes `< 2 minutes`.

## Flag and rollout
- Controlled via the environment flags listed in Rollback plus `FeatureFlagService` (`ff:<env>:flags` in Redis) for runtime feature toggling without redeployment.
- Rollout stages:
  1. Stage 1: Core ASGI runtime, health probes, auth, and polyglot persistence in staging.
  2. Stage 2: Enable inbound overload bulkheads, priority shedding, and Redis rate limiting/caching.
  3. Stage 3: Enable Celery workers (`BACKGROUND_JOBS_ENABLED=true`, `BACKGROUND_WORKER_ENABLED=true`) and Socket.IO gateway (`WEBSOCKET_ENABLED=true`).

## Observability
- **Logs:** `structlog` JSON logs with `requestId`, `context`, `statusCode`, `durationMs`, and AWS X-Ray `trace_id`/`span_id`.
- **Metrics (CloudWatch EMF):** `InboundBulkhead503`, `ApiPriorityShed503`, `InboundLoadLevelQueueFull503`, `InboundLoadLevelWaitTimeout503`, `UnderPressure503`, `DependencyUnavailable503`, `HealthCheck503`, `GraphqlConcurrencyDenied`, and `FeatureFlagEvaluations`.
- **Health Probes:** `GET /health`, `GET /health/ready`, `GET /health/live`.

## Docs impact
- `docs/product-brief.md` (created)
- `docs/nfr.md` (created)
- `docs/architecture.md` (updated)
- `docs/threat-model.md` (updated)
- `docs/observability.md` (updated)
- `docs/adr/0001-stack.md` (created)
- `AGENTS.md`, `README.md`, `CHANGELOG.md` (to be updated with `uv` build/test/lint/run commands during `TASK-001` and `TASK-018`)

## Definition of done
- Requirements `REQ-001` through `REQ-016` are falsifiable and tested across unit, integration, property, and contract suites.
- Every task (`TASK-001` through `TASK-018`) traces directly to `REQ-001..REQ-016`.
- `scripts/verify.sh` passes all 8 quality tiers with zero warnings or errors.
- Independent readonly code, security, and test reviews pass before release.
- Observability (logs, OTel X-Ray traces, CloudWatch EMF metrics, Terminus health probes) and rollback (`.down.sql` migrations and feature toggles) are verified.
