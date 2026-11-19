# Architecture

## System boundaries
- **Inside the system:** FastAPI ASGI application (`app/`), inbound security & overload middlewares, REST controllers/routers, Strawberry GraphQL endpoint (`/graphql`), `python-socketio` real-time gateway (`/socket.io` -> `/realtime`), Celery background job producer & worker consumers, PostgreSQL ACID job ledger & country access repository, MongoDB document repositories (Users, Categories, FeatureFlagAudits), Redis coordination layer (cache, rate limits, JWT revocation, pub/sub, GraphQL budgets, WebSocket connection limits), outbound resilient HTTP client, and OpenTelemetry/CloudWatch EMF instrumentation.
- **Outside the system:** AWS Application Load Balancer (ALB) / reverse proxy, PostgreSQL server, MongoDB server, Redis cluster, ClamAV daemon (`clamd`), MaxMind GeoLite2 `.mmdb` file mount, hCaptcha verification API, Slack Incoming Webhook API, and OTLP trace collector.

## Domain
- **Core (`app/core/`):** Framework-level cross-cutting infrastructure — `shutdown`, `request_deadline`, `overload`, `rate_limiter`, `csrf`, `cache`, `http_client`, `background_jobs`, `websocket`, `feature_flag`, `captcha`, `notification`, `health`, `logger`, `redaction`, `telemetry`, `compression`.
- **Common (`app/common/`):** Shared application policies and adapters — `config` (67-env schema validator), `redis`, `postgres`, `mongo`, `country_access`, `uploads`, `sanitization`, `filters` (global exception mapper), `plugins` (GraphQL complexity/concurrency extension).
- **Feature Modules (`app/auth/`, `app/categories/`, `app/example/`, `app/conformance/`):** Domain modules consuming `core` and `common` abstractions via FastAPI dependency injection.

## Data
- **PostgreSQL (SQLAlchemy 2.0 Async / `asyncpg`):**
  - `country_access_rules`: Country ISO code allow/block rules synced periodically into memory.
  - `background_jobs`: Transactional job ledger with `background_job_status` enum (`pending`, `processing`, `completed`, `failed`, `cancelled`), `FOR UPDATE SKIP LOCKED` worker claims, partial unique active `dedupe_key` index, and `lease_until` timestamps for orphan lease reclamation.
- **MongoDB (Motor + Beanie ODM, lazy connection):**
  - `users`: User credentials (`email`, Argon2id `password_hash`, `roles`), session metadata.
  - `categories`: Category documents queried via REST and GraphQL.
  - `feature_flag_audits`: Immutable audit trail of administrative feature flag mutations.
- **Redis (`redis.asyncio`):**
  - Ephemeral coordination state: `revoked_jti:*`, `session:*`, `ratelimit:*`, `cache:*`, `cache:lock:*`, `gql:budget:*`, `gql:conc:*`, `ws:conn:*`, `ff:flags:*`, plus Redis Pub/Sub channels (`ff:invalidate`, Socket.IO adapter) and Celery broker lists.
- **Personal data & sensitivity:** Documented in [threat-model.md](file:///Users/pc/Documents/personal/fastapi_backend_boilerplate/docs/threat-model.md).

## APIs
- **REST Endpoints:**
  - `GET /` (Hello), `GET /health`, `GET /health/ready`, `GET /health/live`
  - `GET /auth/csrf`, `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`
  - `GET|POST|PUT|DELETE /feature-flags/*` (Admin CRUD)
  - `POST /uploads` (Multipart magic-byte & ClamAV validated uploads)
  - `POST /example/captcha`, `POST /example/notifications`, `POST /example/jobs`
  - `/conformance/*` (`cache`, `deadline`, `http-client`, `priority`, `rate-limit`, `redaction` — disabled when `NODE_ENV=production`)
- **GraphQL (`/graphql`):** Strawberry GraphQL schema serving `categories`, `category(id)`, and `searchCategories(filter)` with depth limit `10` and Redis complexity/concurrency enforcement.
- **WebSockets (`/socket.io`, namespace `/realtime`):** Socket.IO events `join`, `message`, and `admin-broadcast` (`ADMIN` role required).
- **Error Contract:** Uniform JSON `{ statusCode, timestamp, path, requestId, message }` (or Terminus `{ statusCode, timestamp, path, requestId, status, info, error, details }` on health 503s).

## Infrastructure
- **Compute:** Python 3.12+ ASGI server running on Uvicorn (with `uvloop` and `httptools`), plus optional Celery worker processes (`BACKGROUND_WORKER_ENABLED=true`).
- **Middleware & Guard Pipeline Order:**
  1. `CorrelationIdLogMiddleware` -> 2. `UnderPressureMiddleware` -> 3. `SecurityHeadersMiddleware` & `CORS` -> 4. `CompressionMiddleware` -> 5. `GuestIdMiddleware` -> 6. `CsrfMiddleware` -> 7. `RequestDeadlineMiddleware` -> 8. `InboundPriorityBulkheadDependency` -> 9. `JwtAuthDependency` -> 10. `CountryAccessDependency` -> 11. `RateLimitDependency`.

## Non-functional requirements
Measurable targets, SLOs, latency budgets, retention, and compliance live in [nfr.md](file:///Users/pc/Documents/personal/fastapi_backend_boilerplate/docs/nfr.md).
