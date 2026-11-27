from typing import Final, Literal

from pydantic import BaseModel, ConfigDict


class EnvField(BaseModel):
    """Declarative environment variable field metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    required: bool


EnvKey = Literal[
    # Auth (hard-required)
    "JWT_PRIVATE_KEY",
    "JWT_PUBLIC_KEY",
    "COOKIE_SECRET",
    "ACCESS_TOKEN_TTL",
    "SESSION_TTL",
    # Auth password hash bulkhead (overload resilience)
    "AUTH_PASSWORD_HASH_MAX_CONCURRENT",
    "AUTH_PASSWORD_HASH_MAX_QUEUE",
    "AUTH_PASSWORD_HASH_MAX_WAIT_MS",
    # CSRF (cookie-based browser sessions)
    "CSRF_ENABLED",
    "CSRF_EXEMPT_PATHS",
    # MongoDB
    "MONGO_URI",
    "MONGO_PORT",
    # Redis (auth JTI revocation)
    "REDIS_HOST",
    "REDIS_PORT",
    # Application
    "PORT",
    "NODE_ENV",
    "ALLOWED_ORIGINS",
    "HTTP_KEEP_ALIVE_TIMEOUT_MS",
    "HEALTH_CHECK_CACHE_TTL_MS",
    "COMPRESSION_THRESHOLD",
    # Request deadline
    "REQUEST_TIMEOUT_MS",
    "REQUEST_TIMEOUT_MAX_MS",
    # Inbound overload (UnderPressure)
    "UNDER_PRESSURE_ENABLED",
    "UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS",
    "UNDER_PRESSURE_MAX_HEAP_USED_BYTES",
    "UNDER_PRESSURE_MAX_RSS_BYTES",
    "UNDER_PRESSURE_EXPOSE_STATUS_ROUTE",
    "OVERLOAD_RETRY_AFTER_SEC",
    # Inbound fail-fast bulkhead (per VM)
    "INBOUND_BULKHEAD_ENABLED",
    "INBOUND_MAX_IN_FLIGHT",
    "INBOUND_PRIORITY_SHEDDING_ENABLED",
    "INBOUND_DEFAULT_API_PRIORITY",
    "INBOUND_PRIORITY_SHED_CRITICAL_AT",
    "INBOUND_PRIORITY_SHED_HIGH_AT",
    "INBOUND_PRIORITY_SHED_NORMAL_AT",
    "INBOUND_PRIORITY_SHED_LOW_AT",
    "INBOUND_LOAD_LEVELING_ENABLED",
    "INBOUND_LOAD_LEVEL_MAX_QUEUE",
    "INBOUND_LOAD_LEVEL_MAX_WAIT_MS",
    "INBOUND_LOAD_LEVEL_POLL_MS",
    # Graceful shutdown
    "SHUTDOWN_DRAIN_MS",
    "SHUTDOWN_FORCE_MS",
    # Rate limiting / client IP
    "TRUST_PROXY",
    # Multi-VM coordination (rate limit + cache backends)
    "RATE_LIMIT_STORAGE",
    "RATE_LIMIT_FALLBACK_COOLDOWN_MS",
    "CACHE_STORAGE",
    "CACHE_TTL_JITTER_RATIO",
    "CACHE_LOADER_LOCK_TTL_MS",
    "CACHE_LOADER_WAIT_MS",
    "CACHE_NEGATIVE_SOFT_TTL_MS",
    "CACHE_NEGATIVE_HARD_TTL_MS",
    # Country access / GeoIP
    "GEOIP_DATABASE_PATH",
    "COUNTRY_ACCESS_REFRESH_INTERVAL_MS",
    "COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY",
    "COUNTRY_ACCESS_BYPASS_PATHS",
    # Uploads / ClamAV
    "UPLOAD_MAX_FILES",
    "UPLOAD_MAX_FILE_SIZE",
    "UPLOAD_MAX_TOTAL_SIZE",
    "UPLOAD_VIRUS_SCAN",
    "CLAMAV_HOST",
    "CLAMAV_PORT",
    # Postgres (optional; health only when configured)
    "POSTGRES_HOST",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    # Outbound HTTP client
    "HTTP_CLIENT_ALLOW_PRIVATE_URLS",
    "HTTP_CLIENT_INSTANCE_COUNT",
    # hCaptcha (SECRET required when CaptchaModule is loaded)
    "HCAPTCHA_SITE_KEY",
    "HCAPTCHA_SECRET",
    "HCAPTCHA_VERIFY_BASE_URL",
    # Slack Incoming Webhooks
    "SLACK_WEBHOOK_URL",
    # GraphQL complexity budgets
    "GQL_MAX_COMPLEXITY",
    "GQL_USER_BUDGET",
    "GQL_MAX_CONCURRENCY",
    "GQL_CONCURRENCY_KEY_TTL_SEC",
    # WebSocket (Socket.IO)
    "WEBSOCKET_ENABLED",
    "WEBSOCKET_PATH",
    "WEBSOCKET_ALLOW_QUERY_TOKEN",
    "WEBSOCKET_REDIS_ADAPTER",
    "WEBSOCKET_MAX_CONNECTIONS_PER_USER",
    "WEBSOCKET_MAX_INBOUND_BUFFER_BYTES",
    "WEBSOCKET_MESSAGES_PER_MINUTE",
    "WEBSOCKET_CONNECTION_KEY_TTL_SEC",
    # Feature flags
    "FEATURE_FLAG_ENV",
    "FEATURE_FLAG_RECONCILE_MS",
    "FEATURE_FLAG_ADMIN_EMAILS",
    "FEATURE_FLAG_METRICS",
    # Logging
    "LOG_REDACTION_ENABLED",
    # OpenTelemetry / app EMF metrics
    "OTEL_ENABLED",
    "OTEL_SERVICE_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "APP_METRICS_ENABLED",
    # Background jobs
    "BACKGROUND_JOBS_ENABLED",
    "BACKGROUND_WORKER_ENABLED",
    "BACKGROUND_JOBS_DEFAULT_QUEUE",
    "BACKGROUND_JOBS_CLAIM_LEASE_SEC",
    "BACKGROUND_JOBS_WORKER_CONCURRENCY",
    "BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED",
    "BACKGROUND_JOBS_LEASE_RECLAIM_INTERVAL_MS",
    "BACKGROUND_JOBS_LEASE_RECLAIM_BATCH_SIZE",
    "BACKGROUND_JOBS_ORPHAN_MIN_AGE_SEC",
    # Conformance harness (test only; refused in production)
    "CONFORMANCE_HARNESS_ENABLED",
    "CONFORMANCE_HARNESS_TOKEN",
    "RATE_LIMIT_GLOBAL_IP_LIMIT",
]

# Declarative 103-key environment schema
ENV_SCHEMA: Final[dict[str, EnvField]] = {
    # Auth (hard-required)
    "JWT_PRIVATE_KEY": EnvField(required=True),
    "JWT_PUBLIC_KEY": EnvField(required=True),
    "COOKIE_SECRET": EnvField(required=True),
    "ACCESS_TOKEN_TTL": EnvField(required=True),
    "SESSION_TTL": EnvField(required=True),
    # Auth password hash bulkhead (overload resilience)
    "AUTH_PASSWORD_HASH_MAX_CONCURRENT": EnvField(required=False),
    "AUTH_PASSWORD_HASH_MAX_QUEUE": EnvField(required=False),
    "AUTH_PASSWORD_HASH_MAX_WAIT_MS": EnvField(required=False),
    # CSRF (cookie-based browser sessions)
    "CSRF_ENABLED": EnvField(required=False),
    "CSRF_EXEMPT_PATHS": EnvField(required=False),
    # MongoDB
    "MONGO_URI": EnvField(required=True),
    "MONGO_PORT": EnvField(required=False),
    # Redis (auth JTI revocation)
    "REDIS_HOST": EnvField(required=True),
    "REDIS_PORT": EnvField(required=True),
    # Application
    "PORT": EnvField(required=False),
    "NODE_ENV": EnvField(required=False),
    "ALLOWED_ORIGINS": EnvField(required=False),
    "HTTP_KEEP_ALIVE_TIMEOUT_MS": EnvField(required=False),
    "HEALTH_CHECK_CACHE_TTL_MS": EnvField(required=False),
    "COMPRESSION_THRESHOLD": EnvField(required=False),
    # Request deadline
    "REQUEST_TIMEOUT_MS": EnvField(required=False),
    "REQUEST_TIMEOUT_MAX_MS": EnvField(required=False),
    # Inbound overload (UnderPressure)
    "UNDER_PRESSURE_ENABLED": EnvField(required=False),
    "UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS": EnvField(required=False),
    "UNDER_PRESSURE_MAX_HEAP_USED_BYTES": EnvField(required=False),
    "UNDER_PRESSURE_MAX_RSS_BYTES": EnvField(required=False),
    "UNDER_PRESSURE_EXPOSE_STATUS_ROUTE": EnvField(required=False),
    "OVERLOAD_RETRY_AFTER_SEC": EnvField(required=False),
    # Inbound fail-fast bulkhead (per VM)
    "INBOUND_BULKHEAD_ENABLED": EnvField(required=False),
    "INBOUND_MAX_IN_FLIGHT": EnvField(required=False),
    "INBOUND_PRIORITY_SHEDDING_ENABLED": EnvField(required=False),
    "INBOUND_DEFAULT_API_PRIORITY": EnvField(required=False),
    "INBOUND_PRIORITY_SHED_CRITICAL_AT": EnvField(required=False),
    "INBOUND_PRIORITY_SHED_HIGH_AT": EnvField(required=False),
    "INBOUND_PRIORITY_SHED_NORMAL_AT": EnvField(required=False),
    "INBOUND_PRIORITY_SHED_LOW_AT": EnvField(required=False),
    "INBOUND_LOAD_LEVELING_ENABLED": EnvField(required=False),
    "INBOUND_LOAD_LEVEL_MAX_QUEUE": EnvField(required=False),
    "INBOUND_LOAD_LEVEL_MAX_WAIT_MS": EnvField(required=False),
    "INBOUND_LOAD_LEVEL_POLL_MS": EnvField(required=False),
    # Graceful shutdown
    "SHUTDOWN_DRAIN_MS": EnvField(required=False),
    "SHUTDOWN_FORCE_MS": EnvField(required=False),
    # Rate limiting / client IP
    "TRUST_PROXY": EnvField(required=False),
    # Multi-VM coordination (rate limit + cache backends)
    "RATE_LIMIT_STORAGE": EnvField(required=False),
    "RATE_LIMIT_FALLBACK_COOLDOWN_MS": EnvField(required=False),
    "CACHE_STORAGE": EnvField(required=False),
    "CACHE_TTL_JITTER_RATIO": EnvField(required=False),
    "CACHE_LOADER_LOCK_TTL_MS": EnvField(required=False),
    "CACHE_LOADER_WAIT_MS": EnvField(required=False),
    "CACHE_NEGATIVE_SOFT_TTL_MS": EnvField(required=False),
    "CACHE_NEGATIVE_HARD_TTL_MS": EnvField(required=False),
    # Country access / GeoIP
    "GEOIP_DATABASE_PATH": EnvField(required=True),
    "COUNTRY_ACCESS_REFRESH_INTERVAL_MS": EnvField(required=False),
    "COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY": EnvField(required=False),
    "COUNTRY_ACCESS_BYPASS_PATHS": EnvField(required=False),
    # Uploads / ClamAV
    "UPLOAD_MAX_FILES": EnvField(required=False),
    "UPLOAD_MAX_FILE_SIZE": EnvField(required=False),
    "UPLOAD_MAX_TOTAL_SIZE": EnvField(required=False),
    "UPLOAD_VIRUS_SCAN": EnvField(required=False),
    "CLAMAV_HOST": EnvField(required=False),
    "CLAMAV_PORT": EnvField(required=False),
    # Postgres (optional; health only when configured)
    "POSTGRES_HOST": EnvField(required=False),
    "POSTGRES_USER": EnvField(required=False),
    "POSTGRES_PASSWORD": EnvField(required=False),
    "POSTGRES_DB": EnvField(required=False),
    "POSTGRES_PORT": EnvField(required=False),
    # Outbound HTTP client
    "HTTP_CLIENT_ALLOW_PRIVATE_URLS": EnvField(required=False),
    "HTTP_CLIENT_INSTANCE_COUNT": EnvField(required=False),
    # hCaptcha
    "HCAPTCHA_SITE_KEY": EnvField(required=False),
    "HCAPTCHA_SECRET": EnvField(required=True),
    "HCAPTCHA_VERIFY_BASE_URL": EnvField(required=False),
    # Slack Incoming Webhooks
    "SLACK_WEBHOOK_URL": EnvField(required=False),
    # GraphQL complexity budgets
    "GQL_MAX_COMPLEXITY": EnvField(required=False),
    "GQL_USER_BUDGET": EnvField(required=False),
    "GQL_MAX_CONCURRENCY": EnvField(required=False),
    "GQL_CONCURRENCY_KEY_TTL_SEC": EnvField(required=False),
    # WebSocket (Socket.IO)
    "WEBSOCKET_ENABLED": EnvField(required=False),
    "WEBSOCKET_PATH": EnvField(required=False),
    "WEBSOCKET_ALLOW_QUERY_TOKEN": EnvField(required=False),
    "WEBSOCKET_REDIS_ADAPTER": EnvField(required=False),
    "WEBSOCKET_MAX_CONNECTIONS_PER_USER": EnvField(required=False),
    "WEBSOCKET_MAX_INBOUND_BUFFER_BYTES": EnvField(required=False),
    "WEBSOCKET_MESSAGES_PER_MINUTE": EnvField(required=False),
    "WEBSOCKET_CONNECTION_KEY_TTL_SEC": EnvField(required=False),
    # Feature flags
    "FEATURE_FLAG_ENV": EnvField(required=False),
    "FEATURE_FLAG_RECONCILE_MS": EnvField(required=False),
    "FEATURE_FLAG_ADMIN_EMAILS": EnvField(required=False),
    "FEATURE_FLAG_METRICS": EnvField(required=False),
    # Logging
    "LOG_REDACTION_ENABLED": EnvField(required=False),
    # OpenTelemetry / app EMF metrics
    "OTEL_ENABLED": EnvField(required=False),
    "OTEL_SERVICE_NAME": EnvField(required=False),
    "OTEL_EXPORTER_OTLP_ENDPOINT": EnvField(required=False),
    "APP_METRICS_ENABLED": EnvField(required=False),
    # Background jobs
    "BACKGROUND_JOBS_ENABLED": EnvField(required=False),
    "BACKGROUND_WORKER_ENABLED": EnvField(required=False),
    "BACKGROUND_JOBS_DEFAULT_QUEUE": EnvField(required=False),
    "BACKGROUND_JOBS_CLAIM_LEASE_SEC": EnvField(required=False),
    "BACKGROUND_JOBS_WORKER_CONCURRENCY": EnvField(required=False),
    "BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED": EnvField(required=False),
    "BACKGROUND_JOBS_LEASE_RECLAIM_INTERVAL_MS": EnvField(required=False),
    "BACKGROUND_JOBS_LEASE_RECLAIM_BATCH_SIZE": EnvField(required=False),
    "BACKGROUND_JOBS_ORPHAN_MIN_AGE_SEC": EnvField(required=False),
    # Conformance harness (test only; refused in production)
    "CONFORMANCE_HARNESS_ENABLED": EnvField(required=False),
    "CONFORMANCE_HARNESS_TOKEN": EnvField(required=False),
    "RATE_LIMIT_GLOBAL_IP_LIMIT": EnvField(required=False),
}

REQUIRED_ENV_KEYS: Final[tuple[str, ...]] = tuple(
    key for key, field in ENV_SCHEMA.items() if field.required
)
