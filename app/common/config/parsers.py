import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Final, Literal

# ---------------------------------------------------------------------------
# JS-parity primitive parsers
# ---------------------------------------------------------------------------

_JS_WHITESPACE: Final[str] = (
    "\t\n\x0b\x0c\r \u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_JS_INT_RE: Final[re.Pattern[str]] = re.compile(r"^([+-]?[0-9]+)")
_JS_FLOAT_RE: Final[re.Pattern[str]] = re.compile(
    r"^([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
)
_JS_RADIX_INT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+)\Z"
)
_JS_DECIMAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)


def trim_js(value: str) -> str:
    """Mirror ECMAScript String.prototype.trim() (WhiteSpace + LineTerminator)."""
    return value.strip(_JS_WHITESPACE)


def parse_js_number(raw: str) -> float:
    """Mirror ECMAScript Number(raw) semantics for strings."""
    s = trim_js(raw)
    if s == "":
        return 0.0
    if not s.isascii() or "_" in s:
        return math.nan
    if s in ("Infinity", "+Infinity"):
        return math.inf
    if s == "-Infinity":
        return -math.inf
    if _JS_RADIX_INT_RE.match(s) is not None:
        try:
            return float(int(s, 0))
        except OverflowError:
            return math.inf
    if _JS_DECIMAL_RE.match(s) is not None:
        try:
            return float(s)
        except ValueError:
            return math.nan
    return math.nan


def parse_js_int(value: str | None) -> int | None:
    """Mirror JS Number.parseInt(value ?? '', 10)."""
    if not value:
        return None
    match = _JS_INT_RE.match(value.lstrip(_JS_WHITESPACE))
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def parse_js_float(value: str | None) -> float | None:
    """Mirror JS Number.parseFloat(value ?? '')."""
    if not value:
        return None
    match = _JS_FLOAT_RE.match(value.lstrip(_JS_WHITESPACE))
    if match is None:
        return None
    parsed = float(match.group(1))
    return parsed if math.isfinite(parsed) else None


def parse_positive_int(value: str | None, fallback: int) -> int:
    parsed = parse_js_int(value)
    return parsed if parsed is not None and parsed > 0 else fallback


def parse_non_negative_int(value: str | None, fallback: int) -> int:
    parsed = parse_js_int(value)
    return parsed if parsed is not None and parsed >= 0 else fallback


def parse_bool_variant_a(value: str | None, is_default_on: bool) -> bool:
    """Variant A (overload/*): trim+lower; true/1/yes -> True, false/0/no -> False."""
    if value is None or value.strip() == "":
        return is_default_on
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    return is_default_on


def parse_bool_variant_b(value: str | None, is_default_on: bool) -> bool:
    """Variant B (telemetry): None/'' -> default; trim+lower; 1/true vs 0/false."""
    if value is None or value == "":
        return is_default_on
    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    return is_default_on


def parse_bool_variant_c(value: str | None, is_default_on: bool) -> bool:
    """Variant C (websocket): None/'' -> default; exact 'true' or '1' only."""
    if value is None or value == "":
        return is_default_on
    return value in ("true", "1")


# ---------------------------------------------------------------------------
# Overload: UnderPressure, InboundBulkhead, PriorityShedding, InboundLoadLevel
# ---------------------------------------------------------------------------

DEFAULT_OVERLOAD_RETRY_AFTER_SEC: Final[int] = 5
DEFAULT_UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS: Final[int] = 1_000
DEFAULT_INBOUND_MAX_IN_FLIGHT: Final[int] = 100
DEFAULT_INBOUND_LOAD_LEVEL_MAX_QUEUE: Final[int] = 100
DEFAULT_INBOUND_LOAD_LEVEL_MAX_WAIT_MS: Final[int] = 2_000
DEFAULT_INBOUND_LOAD_LEVEL_POLL_MS: Final[int] = 25


@dataclass(frozen=True, slots=True)
class UnderPressureConfig:
    enabled: bool
    max_event_loop_delay_ms: int
    max_heap_used_bytes: int
    max_rss_bytes: int
    retry_after_sec: int
    expose_status_route: bool


def parse_under_pressure_config(
    env: Mapping[str, str | None] | None = None,
) -> UnderPressureConfig:
    target = os.environ if env is None else env
    return UnderPressureConfig(
        enabled=parse_bool_variant_a(target.get("UNDER_PRESSURE_ENABLED"), False),
        max_event_loop_delay_ms=parse_non_negative_int(
            target.get("UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS"),
            DEFAULT_UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS,
        ),
        max_heap_used_bytes=parse_non_negative_int(
            target.get("UNDER_PRESSURE_MAX_HEAP_USED_BYTES"),
            0,
        ),
        max_rss_bytes=parse_non_negative_int(
            target.get("UNDER_PRESSURE_MAX_RSS_BYTES"),
            0,
        ),
        retry_after_sec=max(
            1,
            parse_non_negative_int(
                target.get("OVERLOAD_RETRY_AFTER_SEC"),
                DEFAULT_OVERLOAD_RETRY_AFTER_SEC,
            ),
        ),
        expose_status_route=parse_bool_variant_a(
            target.get("UNDER_PRESSURE_EXPOSE_STATUS_ROUTE"),
            False,
        ),
    )


@dataclass(frozen=True, slots=True)
class InboundBulkheadConfig:
    enabled: bool
    max_in_flight: int


def parse_inbound_bulkhead_config(
    env: Mapping[str, str | None] | None = None,
) -> InboundBulkheadConfig:
    target = os.environ if env is None else env
    return InboundBulkheadConfig(
        enabled=parse_bool_variant_a(target.get("INBOUND_BULKHEAD_ENABLED"), False),
        max_in_flight=parse_positive_int(
            target.get("INBOUND_MAX_IN_FLIGHT"),
            DEFAULT_INBOUND_MAX_IN_FLIGHT,
        ),
    )


class ApiPriorityLevel(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass(frozen=True, slots=True)
class PrioritySheddingThresholds:
    critical: float
    high: float
    normal: float
    low: float


DEFAULT_PRIORITY_SHEDDING_THRESHOLDS: Final[PrioritySheddingThresholds] = (
    PrioritySheddingThresholds(
        critical=1.0,
        high=0.95,
        normal=0.85,
        low=0.7,
    )
)


@dataclass(frozen=True, slots=True)
class PrioritySheddingConfig:
    enabled: bool
    default_priority: ApiPriorityLevel
    thresholds: PrioritySheddingThresholds


def _parse_shedding_ratio(value: str | None, fallback: float) -> float:
    parsed = parse_js_float(value)
    if parsed is None or parsed <= 0 or parsed > 1:
        return fallback
    return parsed


def _parse_default_priority(value: str | None) -> ApiPriorityLevel:
    normalized = (value or "").strip().lower()
    if normalized == "critical":
        return ApiPriorityLevel.CRITICAL
    if normalized == "high":
        return ApiPriorityLevel.HIGH
    if normalized == "low":
        return ApiPriorityLevel.LOW
    return ApiPriorityLevel.NORMAL


def parse_priority_shedding_config(
    env: Mapping[str, str | None] | None = None,
) -> PrioritySheddingConfig:
    target = os.environ if env is None else env
    return PrioritySheddingConfig(
        enabled=parse_bool_variant_a(
            target.get("INBOUND_PRIORITY_SHEDDING_ENABLED"),
            False,
        ),
        default_priority=_parse_default_priority(
            target.get("INBOUND_DEFAULT_API_PRIORITY")
        ),
        thresholds=PrioritySheddingThresholds(
            critical=_parse_shedding_ratio(
                target.get("INBOUND_PRIORITY_SHED_CRITICAL_AT"),
                DEFAULT_PRIORITY_SHEDDING_THRESHOLDS.critical,
            ),
            high=_parse_shedding_ratio(
                target.get("INBOUND_PRIORITY_SHED_HIGH_AT"),
                DEFAULT_PRIORITY_SHEDDING_THRESHOLDS.high,
            ),
            normal=_parse_shedding_ratio(
                target.get("INBOUND_PRIORITY_SHED_NORMAL_AT"),
                DEFAULT_PRIORITY_SHEDDING_THRESHOLDS.normal,
            ),
            low=_parse_shedding_ratio(
                target.get("INBOUND_PRIORITY_SHED_LOW_AT"),
                DEFAULT_PRIORITY_SHEDDING_THRESHOLDS.low,
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class InboundLoadLevelConfig:
    enabled: bool
    max_queue: int
    max_wait_ms: int
    poll_interval_ms: int


def parse_inbound_load_level_config(
    env: Mapping[str, str | None] | None = None,
) -> InboundLoadLevelConfig:
    target = os.environ if env is None else env
    return InboundLoadLevelConfig(
        enabled=parse_bool_variant_a(
            target.get("INBOUND_LOAD_LEVELING_ENABLED"),
            False,
        ),
        max_queue=parse_positive_int(
            target.get("INBOUND_LOAD_LEVEL_MAX_QUEUE"),
            DEFAULT_INBOUND_LOAD_LEVEL_MAX_QUEUE,
        ),
        max_wait_ms=parse_positive_int(
            target.get("INBOUND_LOAD_LEVEL_MAX_WAIT_MS"),
            DEFAULT_INBOUND_LOAD_LEVEL_MAX_WAIT_MS,
        ),
        poll_interval_ms=parse_positive_int(
            target.get("INBOUND_LOAD_LEVEL_POLL_MS"),
            DEFAULT_INBOUND_LOAD_LEVEL_POLL_MS,
        ),
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

DEFAULT_WEBSOCKET_PATH: Final[str] = "/socket.io"
DEFAULT_WEBSOCKET_MAX_CONNECTIONS_PER_USER: Final[int] = 5
DEFAULT_WEBSOCKET_MAX_INBOUND_BUFFER_BYTES: Final[int] = 1_000_000
DEFAULT_WEBSOCKET_MESSAGES_PER_MINUTE: Final[int] = 120
DEFAULT_WEBSOCKET_CONNECTION_KEY_TTL_SEC: Final[int] = 86_400


@dataclass(frozen=True, slots=True)
class WebSocketConfig:
    enabled: bool
    path: str
    allow_query_token: bool
    redis_adapter_enabled: bool
    cors_origins: bool | list[str]
    max_connections_per_user: int
    max_inbound_buffer_bytes: int
    messages_per_minute: int
    connection_key_ttl_sec: int


def _resolve_ws_cors_origins(
    env: Mapping[str, str | None],
) -> bool | list[str]:
    raw_node_env = env.get("NODE_ENV")
    node_env = "development" if raw_node_env is None else raw_node_env
    raw = env.get("ALLOWED_ORIGINS")
    origins = (
        [part.strip() for part in raw.split(",") if part.strip()]
        if raw is not None
        else []
    )
    if node_env == "production":
        return origins
    if not origins:
        return True
    return origins


def parse_websocket_config(
    env: Mapping[str, str | None] | None = None,
) -> WebSocketConfig:
    target = os.environ if env is None else env
    raw_node_env = target.get("NODE_ENV")
    node_env = "development" if raw_node_env is None else raw_node_env

    redis_adapter_explicit = target.get("WEBSOCKET_REDIS_ADAPTER")
    if redis_adapter_explicit is not None and redis_adapter_explicit != "":
        is_redis_adapter_enabled = parse_bool_variant_c(redis_adapter_explicit, False)
    else:
        is_redis_adapter_enabled = node_env == "production"

    raw_path = target.get("WEBSOCKET_PATH")
    path = raw_path.strip() if raw_path is not None and raw_path.strip() else None

    return WebSocketConfig(
        enabled=parse_bool_variant_c(target.get("WEBSOCKET_ENABLED"), True),
        path=path if path is not None else DEFAULT_WEBSOCKET_PATH,
        allow_query_token=parse_bool_variant_c(
            target.get("WEBSOCKET_ALLOW_QUERY_TOKEN"),
            False,
        ),
        redis_adapter_enabled=is_redis_adapter_enabled,
        cors_origins=_resolve_ws_cors_origins(target),
        max_connections_per_user=parse_positive_int(
            target.get("WEBSOCKET_MAX_CONNECTIONS_PER_USER"),
            DEFAULT_WEBSOCKET_MAX_CONNECTIONS_PER_USER,
        ),
        max_inbound_buffer_bytes=parse_positive_int(
            target.get("WEBSOCKET_MAX_INBOUND_BUFFER_BYTES"),
            DEFAULT_WEBSOCKET_MAX_INBOUND_BUFFER_BYTES,
        ),
        messages_per_minute=parse_positive_int(
            target.get("WEBSOCKET_MESSAGES_PER_MINUTE"),
            DEFAULT_WEBSOCKET_MESSAGES_PER_MINUTE,
        ),
        connection_key_ttl_sec=parse_positive_int(
            target.get("WEBSOCKET_CONNECTION_KEY_TTL_SEC"),
            DEFAULT_WEBSOCKET_CONNECTION_KEY_TTL_SEC,
        ),
    )


def assert_websocket_production_origins(config: WebSocketConfig, node_env: str) -> None:
    if not config.enabled or node_env != "production":
        return
    if isinstance(config.cors_origins, list) and len(config.cors_origins) == 0:
        raise ValueError(
            "ALLOWED_ORIGINS is required for WebSocket when NODE_ENV=production"
        )


def assert_rest_production_origins(raw_origins: str | None, node_env: str) -> None:
    if node_env != "production":
        return
    if raw_origins is None or trim_js(str(raw_origins)) == "":
        raise ValueError(
            "ALLOWED_ORIGINS must list explicit origins when NODE_ENV=production"
        )
    origins = [part.strip() for part in raw_origins.split(",") if part.strip()]
    if not origins or any(origin == "*" for origin in origins):
        raise ValueError(
            "ALLOWED_ORIGINS must list explicit origins when NODE_ENV=production"
        )


def is_websocket_enabled(env: Mapping[str, str | None] | None = None) -> bool:
    target = os.environ if env is None else env
    return parse_bool_variant_c(target.get("WEBSOCKET_ENABLED"), True)


# ---------------------------------------------------------------------------
# Background Jobs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackgroundJobsPostgresConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True, slots=True)
class BackgroundJobsConfig:
    enabled: bool
    worker_enabled: bool
    default_queue_name: str
    claim_lease_sec: int
    worker_concurrency: int
    lease_reclaim_enabled: bool
    lease_reclaim_interval_ms: int
    lease_reclaim_batch_size: int
    orphan_min_age_sec: int
    postgres: BackgroundJobsPostgresConfig


def parse_background_jobs_config(
    env: Mapping[str, str | None],
) -> BackgroundJobsConfig:
    is_enabled = env.get("BACKGROUND_JOBS_ENABLED") == "true"
    is_worker_enabled = env.get("BACKGROUND_WORKER_ENABLED") == "true"

    raw_queue = env.get("BACKGROUND_JOBS_DEFAULT_QUEUE")
    default_queue_name = (
        raw_queue.strip() if raw_queue is not None and raw_queue.strip() else "default"
    )

    raw_host = env.get("POSTGRES_HOST")
    raw_user = env.get("POSTGRES_USER")
    raw_password = env.get("POSTGRES_PASSWORD")
    raw_db = env.get("POSTGRES_DB")

    postgres = BackgroundJobsPostgresConfig(
        host=raw_host.strip()
        if raw_host is not None and raw_host.strip()
        else "localhost",
        port=parse_positive_int(env.get("POSTGRES_PORT"), 5432),
        user=raw_user.strip() if raw_user is not None and raw_user.strip() else "",
        password="" if raw_password is None else raw_password,
        database=raw_db.strip() if raw_db is not None and raw_db.strip() else "",
    )

    if is_enabled and not postgres.database:
        raise ValueError("POSTGRES_DB is required when BACKGROUND_JOBS_ENABLED=true")

    return BackgroundJobsConfig(
        enabled=is_enabled,
        worker_enabled=is_worker_enabled,
        default_queue_name=default_queue_name,
        claim_lease_sec=parse_positive_int(
            env.get("BACKGROUND_JOBS_CLAIM_LEASE_SEC"), 300
        ),
        worker_concurrency=parse_positive_int(
            env.get("BACKGROUND_JOBS_WORKER_CONCURRENCY"), 5
        ),
        lease_reclaim_enabled=env.get("BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED")
        != "false",
        lease_reclaim_interval_ms=parse_positive_int(
            env.get("BACKGROUND_JOBS_LEASE_RECLAIM_INTERVAL_MS"), 30_000
        ),
        lease_reclaim_batch_size=parse_positive_int(
            env.get("BACKGROUND_JOBS_LEASE_RECLAIM_BATCH_SIZE"), 100
        ),
        orphan_min_age_sec=parse_positive_int(
            env.get("BACKGROUND_JOBS_ORPHAN_MIN_AGE_SEC"), 30
        ),
        postgres=postgres,
    )


def is_background_jobs_enabled(
    env: Mapping[str, str | None] | None = None,
) -> bool:
    target = os.environ if env is None else env
    return target.get("BACKGROUND_JOBS_ENABLED") == "true"


# ---------------------------------------------------------------------------
# Country Access / GeoIP
# ---------------------------------------------------------------------------

DEFAULT_COUNTRY_ACCESS_REFRESH_INTERVAL_MS: Final[int] = 60_000
DEFAULT_COUNTRY_ACCESS_UNKNOWN_POLICY: Final[Literal["block", "allow"]] = "block"
DEFAULT_COUNTRY_ACCESS_BYPASS_PATHS: Final[tuple[str, ...]] = (
    "/health",
    "/health/ready",
)


@dataclass(frozen=True, slots=True)
class CountryAccessPostgresConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True, slots=True)
class CountryAccessModuleOptions:
    refresh_interval_ms: int
    geoip_database_path: str
    unknown_country_policy: Literal["block", "allow"]
    bypass_paths: list[str]
    persistence_provider: str
    postgres: CountryAccessPostgresConfig


def _parse_strict_positive_int(raw: str | None, fallback: int) -> int:
    if raw is None or raw.strip() == "":
        return fallback
    parsed = parse_js_int(raw)
    if parsed is None or parsed <= 0:
        raise ValueError(f"Invalid positive integer configuration value: {raw}")
    return parsed


def _parse_unknown_policy(raw: str | None) -> Literal["block", "allow"]:
    effective = DEFAULT_COUNTRY_ACCESS_UNKNOWN_POLICY if raw is None else raw
    normalized = effective.strip().lower()
    if normalized == "block":
        return "block"
    if normalized == "allow":
        return "allow"
    raise ValueError(
        f"Invalid COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY: {raw}. "
        'Expected "block" or "allow".'
    )


def _parse_bypass_paths(raw: str | None) -> list[str]:
    if raw is None or raw.strip() == "":
        return list(DEFAULT_COUNTRY_ACCESS_BYPASS_PATHS)
    paths: list[str] = []
    for segment in raw.split(","):
        trimmed = segment.strip()
        if not trimmed:
            continue
        paths.append(trimmed if trimmed.startswith("/") else f"/{trimmed}")
    return paths


def parse_country_access_options(
    env: Mapping[str, str | None],
    persistence_provider: str = "postgres",
) -> CountryAccessModuleOptions:
    raw_geoip = env.get("GEOIP_DATABASE_PATH")
    geoip_path = raw_geoip.strip() if raw_geoip is not None else ""
    if not geoip_path:
        raise ValueError("GEOIP_DATABASE_PATH is required")

    raw_host = env.get("POSTGRES_HOST")
    raw_user = env.get("POSTGRES_USER")
    raw_password = env.get("POSTGRES_PASSWORD")
    raw_db = env.get("POSTGRES_DB")

    return CountryAccessModuleOptions(
        refresh_interval_ms=_parse_strict_positive_int(
            env.get("COUNTRY_ACCESS_REFRESH_INTERVAL_MS"),
            DEFAULT_COUNTRY_ACCESS_REFRESH_INTERVAL_MS,
        ),
        geoip_database_path=geoip_path,
        unknown_country_policy=_parse_unknown_policy(
            env.get("COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY")
        ),
        bypass_paths=_parse_bypass_paths(env.get("COUNTRY_ACCESS_BYPASS_PATHS")),
        persistence_provider=persistence_provider,
        postgres=CountryAccessPostgresConfig(
            host=raw_host.strip()
            if raw_host is not None and raw_host.strip()
            else "localhost",
            port=_parse_strict_positive_int(env.get("POSTGRES_PORT"), 5432),
            user=raw_user.strip()
            if raw_user is not None and raw_user.strip()
            else "user",
            password="password" if raw_password is None else raw_password,
            database=raw_db.strip()
            if raw_db is not None and raw_db.strip()
            else "nestjs_db",
        ),
    )


# ---------------------------------------------------------------------------
# Telemetry & Log Redaction
# ---------------------------------------------------------------------------

DEFAULT_OTEL_SERVICE_NAME: Final[str] = "nestjs-boilerplate"


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    otel_enabled: bool
    service_name: str
    app_metrics_enabled: bool
    otlp_endpoint: str | None = None


def parse_telemetry_config(
    env: Mapping[str, str | None] | None = None,
) -> TelemetryConfig:
    target = os.environ if env is None else env
    raw_endpoint = target.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    otlp_endpoint = (
        raw_endpoint.strip()
        if raw_endpoint is not None and raw_endpoint.strip()
        else None
    )
    raw_service = target.get("OTEL_SERVICE_NAME")
    service_name = (
        raw_service.strip()
        if raw_service is not None and raw_service.strip()
        else DEFAULT_OTEL_SERVICE_NAME
    )

    return TelemetryConfig(
        otel_enabled=parse_bool_variant_b(target.get("OTEL_ENABLED"), False),
        service_name=service_name,
        app_metrics_enabled=parse_bool_variant_b(
            target.get("APP_METRICS_ENABLED"), True
        ),
        otlp_endpoint=otlp_endpoint,
    )


def is_app_metrics_enabled(config: TelemetryConfig) -> bool:
    return config.app_metrics_enabled


@dataclass(frozen=True, slots=True)
class LogRedactionConfig:
    enabled: bool


def parse_log_redaction_config(
    env: Mapping[str, str | None],
) -> LogRedactionConfig:
    return LogRedactionConfig(enabled=env.get("LOG_REDACTION_ENABLED") != "false")


# ---------------------------------------------------------------------------
# Inline Config Helpers (HTTP client, CSRF CSV, Deadlines)
# ---------------------------------------------------------------------------


def parse_http_client_instance_count(
    env_or_value: Mapping[str, str | None] | str | int | None = None,
) -> int:
    if env_or_value is None or isinstance(env_or_value, bool):
        raw: str | None = (
            os.environ.get("HTTP_CLIENT_INSTANCE_COUNT")
            if env_or_value is None
            else None
        )
        parsed = parse_js_int("1" if raw is None else raw)
        return parsed if parsed is not None and parsed >= 1 else 1
    if isinstance(env_or_value, int):
        return env_or_value if env_or_value >= 1 else 1
    if isinstance(env_or_value, str):
        parsed = parse_js_int(env_or_value)
        return parsed if parsed is not None and parsed >= 1 else 1
    raw_val = env_or_value.get("HTTP_CLIENT_INSTANCE_COUNT")
    parsed = parse_js_int("1" if raw_val is None else raw_val)
    return parsed if parsed is not None and parsed >= 1 else 1


def parse_csv_paths(raw: str | None, defaults: Sequence[str]) -> list[str]:
    if raw is None or raw.strip() == "":
        return list(defaults)
    extra = [segment.strip() for segment in raw.split(",") if segment.strip()]
    return [*defaults, *extra]


def parse_positive_timeout_ms(raw: object, fallback: int) -> int:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        num = float(raw)
    elif raw is None:
        return fallback
    else:
        num = parse_js_number(str(raw))

    if not math.isfinite(num) or num <= 0:
        return fallback
    return math.floor(num)


@dataclass(frozen=True, slots=True)
class ResolvedTimeout:
    timeout_ms: int | None
    skipped: bool


def resolve_timeout_ms(
    *,
    header_ms: int | None = None,
    route_override_ms: int | None = None,
    default_timeout_ms: int,
    max_timeout_ms: int,
    skipped: bool = False,
) -> ResolvedTimeout:
    if skipped:
        return ResolvedTimeout(timeout_ms=None, skipped=True)
    server_budget = (
        route_override_ms if route_override_ms is not None else default_timeout_ms
    )
    with_client = (
        min(header_ms, server_budget) if header_ms is not None else server_budget
    )
    timeout_ms = min(with_client, max_timeout_ms)
    return ResolvedTimeout(timeout_ms=max(1, timeout_ms), skipped=False)
