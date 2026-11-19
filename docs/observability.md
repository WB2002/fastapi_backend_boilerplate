# Observability

## Structured log fields
All application logs are emitted as single-line JSON via `structlog` (with recursive PII redaction when `LOG_REDACTION_ENABLED=true`):
- `timestamp`: ISO-8601 UTC timestamp (`YYYY-MM-DDTHH:mm:ss.sssZ`)
- `level`: `info` | `warn` | `error` | `debug`
- `context`: Component/service name (e.g., `HTTP`, `AllExceptionsFilter`, `JobEnqueueService`, `FeatureFlagService`)
- `requestId`: Correlation ID extracted from `X-Request-Id` header or generated UUIDv4
- `trace_id` / `span_id`: AWS X-Ray formatted OpenTelemetry trace and span identifiers (when `OTEL_ENABLED=true`)
- `method`, `url`, `statusCode`, `durationMs`: HTTP access log fields

## RED metrics
Rate, errors, duration, and overload telemetry are emitted via OpenTelemetry spans (`OTEL_EXPORTER_OTLP_ENDPOINT`) and AWS CloudWatch Embedded Metric Format (EMF) JSON logs (`APP_METRICS_ENABLED=true`, namespace `BackendBoilerplate/Overload` and `BackendBoilerplate/FeatureFlags`):
- `InboundBulkhead503`: Emitted when concurrent in-flight requests exceed `INBOUND_MAX_IN_FLIGHT`.
- `ApiPriorityShed503`: Emitted when progressive priority shedding rejects low/normal/high traffic.
- `InboundLoadLevelQueueFull503` / `InboundLoadLevelWaitTimeout503`: Emitted when the HTTP wait queue overflows or times out.
- `UnderPressure503`: Emitted when event-loop lag or RSS/heap thresholds trigger load shedding.
- `DependencyUnavailable503` / `HealthCheck503`: Emitted when Mongo/Postgres/Redis connectivity fails or `/health` returns 503.
- `GraphqlConcurrencyDenied`: Emitted when per-user GraphQL active execution cap (`GQL_MAX_CONCURRENCY`) is exceeded.
- `FeatureFlagEvaluations`: Emitted when `FEATURE_FLAG_METRICS=true` to track flag evaluation volume.

## Alert to runbook
- **High 503 Overload Rate (`InboundBulkhead503` / `ApiPriorityShed503` / `UnderPressure503`):** Inspect upstream traffic spikes, downstream database latency, and worker CPU utilization (`docs/runbooks/`).
- **Readiness Probe Failure (`HealthCheck503`):** Verify MongoDB (`MONGO_URI`), PostgreSQL (`POSTGRES_*`), Redis (`REDIS_*`), and disk/memory thresholds.
- **Stalled Background Jobs (`background_jobs` `status='processing'` with expired `lease_until`):** Verify Celery worker health and `BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED` sweep logs.

## Error tracking
All uncaught exceptions pass through `AllExceptionsFilter`, which maps domain errors (deadlines `504`/`499`, overload `503` + `Retry-After`, DB connection `503`, Terminus health `503`, and standard `HttpException` statuses) into deterministic JSON payloads with `requestId` correlation and redacted error logs.
