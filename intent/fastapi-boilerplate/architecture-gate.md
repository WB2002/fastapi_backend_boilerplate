# Architecture gate: FastAPI Enterprise Backend Boilerplate (NestJS Parity)
Source: intent/fastapi-boilerplate/spec.md
Status: accepted
Lifecycle: DRAFT
Recommendation: PASS

## Checklist
- Requirements complete: PASS — All 16 functional and non-functional domains from the NestJS reference codebase and Google Doc (`REQ-001` through `REQ-016`) are explicitly specified with falsifiable acceptance criteria and 67 environment variables mapped 1:1.
- Architecture coherent: PASS — Clean separation between `app/core/` (framework cross-cutting infrastructure), `app/common/` (shared persistence, config, sanitization, uploads, country access, exception filters, GraphQL plugins), and domain modules (`app/auth/`, `app/categories/`, `app/example/`, `app/conformance/`).
- Boundaries reasonable: PASS — Middleware execution order and dependency guards mirror the exact NestJS pipeline (Correlation ID -> UnderPressure -> Helmet/CORS -> Compression -> GuestId -> CSRF -> RequestDeadline -> InboundPriorityBulkhead -> JwtAuth -> CountryAccess -> RateLimit -> Handler).
- Failure modes considered: PASS — Lazy MongoDB connection prevents startup/shutdown hangs; Redis rate-limit and feature-flag stores degrade gracefully to in-memory snapshots on Redis failure; Argon2id hashing is isolated behind an async concurrency bulkhead; Slack notifications fail open; outbound HTTP calls enforce circuit breakers, retries with full jitter, adaptive concurrency, and deadlines.
- Data consistency correct: PASS — Background jobs use PostgreSQL ACID transactions (`FOR UPDATE SKIP LOCKED`, partial unique index on active `dedupe_key`, and periodic expired-lease reclaim) before waking Celery Redis workers, preventing lost or duplicate jobs even if Redis restarts or a worker crashes mid-flight.
- Scalability adequate: PASS — Multi-VM horizontal scaling is supported out of the box via Redis-backed rate limiting, distributed SWR cache loader locks (`SET NX PX`), Redis Pub/Sub feature flag invalidation, `AsyncRedisManager` for Socket.IO cross-node broadcasts, and cluster-aware outbound HTTP concurrency limits (`HTTP_CLIENT_INSTANCE_COUNT`).
- Security considered: PASS — Global default-deny RS256 JWT guard with explicit `@public` opt-out, Redis JTI revocation, Argon2id CPU bulkhead, double-submit HMAC CSRF, pre-validation `nh3` HTML sanitization, outbound DNS/IP SSRF blocking, GraphQL depth/complexity/budget/concurrency limits, magic-byte + ClamAV upload verification, MaxMind GeoIP filtering, and recursive PII log redaction.
- Observability defined: PASS — Terminus-compatible `/health`, `/health/ready`, and `/health/live` probes with `HealthSnapshot` caching (`2000ms`), `structlog` JSON logs with `X-Request-Id`, OpenTelemetry AWS X-Ray trace propagation, and CloudWatch EMF metric emission for all 503 overload and feature flag events.
- Operational model defined: PASS — Two-phase `SIGTERM` graceful shutdown (`SHUTDOWN_DRAIN_MS=10000` ALB drain after flipping `/health/ready` to 503, followed by `SHUTDOWN_FORCE_MS=5000` bounded teardown), deterministic SQL migrations, and an 8-tier `scripts/verify.sh` pipeline.

## Recommendation
PASS — Ready for human review and acceptance (`Status: accepted`) alongside `docs/adr/0001-stack.md` and `intent/fastapi-boilerplate/intent.md`. No blocking architectural gaps remain.
