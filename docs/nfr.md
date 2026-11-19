# Non-functional requirements

## SLOs
- **Availability:** 99.95% monthly uptime for core REST, GraphQL, and WebSocket endpoints (excluding planned maintenance).
- **Health Probe Reliability:** `/health/live` and `/health/ready` respond within `50ms` (cached snapshot TTL `HEALTH_CHECK_CACHE_TTL_MS=2000ms`), returning HTTP `503` immediately upon `SIGTERM` so AWS ALB drains traffic cleanly without dropped in-flight requests.
- **Overload Shedding Accuracy:** 100% of rejected requests during concurrency saturation, event-loop lag (`>1000ms`), or priority shedding return HTTP `503 Service Unavailable` with a valid `Retry-After` header (`OVERLOAD_RETRY_AFTER_SEC=5`) and emit a CloudWatch EMF metric document.

## Latency budgets
- **Middleware & Guard Overhead:** `< 3ms` p99 total combined latency for correlation ID logging, guest ID signing, CSRF double-submit verification, JWT RS256 verification, in-memory GeoIP lookup, and rate-limit check.
- **Default Request Deadline:** `REQUEST_TIMEOUT_MS=30000ms` (30s default), configurable per-route or via `X-Request-Timeout-Ms` header up to `REQUEST_TIMEOUT_MAX_MS=120000ms` (120s). Expired requests abort downstream work and return HTTP `504 Gateway Timeout`; client disconnects return HTTP `499 Client Closed Request`.
- **Password Hashing Bulkhead:** Argon2id hashing capped at `AUTH_PASSWORD_HASH_MAX_CONCURRENT=2` active threads/workers, queue depth `32`, and max queue wait `AUTH_PASSWORD_HASH_MAX_WAIT_MS=2000ms` before failing fast with HTTP `503`.
- **Cache Stampede Wait Budget:** Parallel readers wait at most `CACHE_LOADER_WAIT_MS=5000ms` for the distributed Redis loader lock (`CACHE_LOADER_LOCK_TTL_MS=30000ms`) before falling back or serving stale data.
- **Database Connect Fail-Fast:** MongoDB (`serverSelectionTimeoutMS=2000ms`, `connectTimeoutMS=2000ms`) and PostgreSQL (`command_timeout=2.0s` on health probes) never block application boot or hang graceful shutdown.
- **Graceful Shutdown Budget:** `SHUTDOWN_DRAIN_MS=10000ms` (10s ALB drain window after `/health/ready` flips to 503) followed by `SHUTDOWN_FORCE_MS=5000ms` (5s max teardown window for sockets, Celery producer, and DB pools).

## Retention
- **Access & Session Tokens:** JWT access tokens expire in `ACCESS_TOKEN_TTL=900s` (15 minutes). Refresh sessions and Redis `revoked_jti:*` entries expire in `SESSION_TTL=604800s` (7 days).
- **WebSocket Connection Leases:** Redis per-user socket tracking keys expire in `WEBSOCKET_CONNECTION_KEY_TTL_SEC=86400s` (24 hours).
- **GraphQL Concurrency & Budget Keys:** Concurrency leases expire in `GQL_CONCURRENCY_KEY_TTL_SEC=300s`; complexity budget windows roll every `60s`.
- **Audit Logs & Job Ledger:** MongoDB `feature_flag_audits` and PostgreSQL `background_jobs` rows are retained indefinitely in application storage until archived by database lifecycle policies.

## Compliance
- **PII Redaction (`LOG_REDACTION_ENABLED=true`):** Automated recursive masking of sensitive keys (`password`, `token`, `secret`, `authorization`, `cookie`, `ssn`, `creditCard`) and regex patterns (emails, Bearer tokens, JWTs) before log emission.
- **Supply Chain & Static Security:** Zero high/critical vulnerabilities in `pip-audit` and `osv-scanner`; zero secrets committed (`gitleaks`); zero high-severity findings in `semgrep` OWASP rules.
- **Cryptography:** Asymmetric RS256 JWT signatures, Argon2id password hashing, and HMAC-SHA256 cookie/CSRF signing.
