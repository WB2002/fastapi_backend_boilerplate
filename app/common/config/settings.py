import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from app.common.config.parse_cache_module_options import (
    CacheModuleOptions,
    parse_cache_module_options,
)
from app.common.config.parse_http_server_timeouts import (
    HttpServerTimeouts,
    parse_http_server_timeouts,
)
from app.common.config.parse_trust_proxy import parse_trust_proxy
from app.common.config.parsers import (
    BackgroundJobsConfig,
    CountryAccessModuleOptions,
    InboundBulkheadConfig,
    InboundLoadLevelConfig,
    LogRedactionConfig,
    PrioritySheddingConfig,
    TelemetryConfig,
    UnderPressureConfig,
    WebSocketConfig,
    parse_background_jobs_config,
    parse_country_access_options,
    parse_http_client_instance_count,
    parse_inbound_bulkhead_config,
    parse_inbound_load_level_config,
    parse_js_int,
    parse_js_number,
    parse_log_redaction_config,
    parse_non_negative_int,
    parse_positive_int,
    parse_positive_timeout_ms,
    parse_priority_shedding_config,
    parse_telemetry_config,
    parse_under_pressure_config,
    parse_websocket_config,
)
from app.common.config.resolve_coordination_storage import (
    CacheStorage,
    RateLimitStorage,
    resolve_cache_storage_from_config,
    resolve_rate_limit_storage_from_config,
)
from app.common.config.validate_env import (
    is_conformance_harness_enabled,
    validate_env,
)


def normalize_pem(value: str) -> str:
    """Convert escaped '\\n' sequences into literal newlines for PEM keys."""
    return value.replace("\\n", "\n")


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated application settings backed by the 103-var ENV_SCHEMA."""

    _env: dict[str, str]

    @classmethod
    def from_env(cls, raw_env: Mapping[str, str | None] | None = None) -> "Settings":
        source: Mapping[str, str | None] = os.environ if raw_env is None else raw_env
        validated = validate_env(source)
        return cls(_env=validated)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._env.get(key, default)

    def as_dict(self) -> dict[str, str]:
        return dict(self._env)

    # Core Application
    @property
    def node_env(self) -> str:
        return self._env.get("NODE_ENV", "development")

    @property
    def port(self) -> int:
        return parse_positive_int(self._env.get("PORT"), 3000)

    @property
    def allowed_origins(self) -> str | None:
        return self._env.get("ALLOWED_ORIGINS")

    @property
    def compression_threshold(self) -> int:
        return parse_non_negative_int(self._env.get("COMPRESSION_THRESHOLD"), 1024)

    @property
    def health_check_cache_ttl_ms(self) -> int:
        parsed = parse_js_int(self._env.get("HEALTH_CHECK_CACHE_TTL_MS"))
        return parsed if parsed is not None and parsed >= 0 else 2000

    # Auth & Security
    @property
    def jwt_private_key(self) -> str:
        return normalize_pem(self._env["JWT_PRIVATE_KEY"])

    @property
    def jwt_public_key(self) -> str:
        return normalize_pem(self._env["JWT_PUBLIC_KEY"])

    @property
    def cookie_secret(self) -> str:
        return self._env["COOKIE_SECRET"]

    @property
    def access_token_ttl(self) -> int:
        return parse_positive_int(self._env.get("ACCESS_TOKEN_TTL"), 900)

    @property
    def session_ttl(self) -> int:
        return parse_positive_int(self._env.get("SESSION_TTL"), 604_800)

    @property
    def auth_password_hash_max_concurrent(self) -> int:
        return parse_positive_int(self._env.get("AUTH_PASSWORD_HASH_MAX_CONCURRENT"), 2)

    @property
    def auth_password_hash_max_queue(self) -> int:
        return parse_non_negative_int(self._env.get("AUTH_PASSWORD_HASH_MAX_QUEUE"), 32)

    @property
    def auth_password_hash_max_wait_ms(self) -> int:
        return parse_non_negative_int(
            self._env.get("AUTH_PASSWORD_HASH_MAX_WAIT_MS"), 2000
        )

    @property
    def csrf_enabled(self) -> bool:
        return self._env.get("CSRF_ENABLED") != "false"

    @property
    def csrf_exempt_paths(self) -> str | None:
        return self._env.get("CSRF_EXEMPT_PATHS")

    # Persistence
    @property
    def mongo_uri(self) -> str:
        return self._env["MONGO_URI"]

    @property
    def mongo_port(self) -> int:
        return parse_positive_int(self._env.get("MONGO_PORT"), 27017)

    @property
    def redis_host(self) -> str:
        return self._env["REDIS_HOST"]

    @property
    def redis_port(self) -> int:
        return parse_positive_int(self._env.get("REDIS_PORT"), 6379)

    # Deadlines & Graceful Shutdown
    @property
    def request_timeout_ms(self) -> int:
        return parse_positive_timeout_ms(self._env.get("REQUEST_TIMEOUT_MS"), 30_000)

    @property
    def request_timeout_max_ms(self) -> int:
        default_ms = self.request_timeout_ms
        parsed = parse_positive_timeout_ms(
            self._env.get("REQUEST_TIMEOUT_MAX_MS"), 120_000
        )
        return max(default_ms, parsed)

    @property
    def shutdown_drain_ms(self) -> int:
        return parse_non_negative_int(self._env.get("SHUTDOWN_DRAIN_MS"), 10_000)

    @property
    def shutdown_force_ms(self) -> int:
        return parse_non_negative_int(self._env.get("SHUTDOWN_FORCE_MS"), 5_000)

    # Feature Flags
    @property
    def feature_flag_env(self) -> str:
        raw = self._env.get("FEATURE_FLAG_ENV")
        if raw is not None and raw.strip():
            return raw.strip()
        return self.node_env or "development"

    @property
    def feature_flag_reconcile_ms(self) -> int:
        raw = self._env.get("FEATURE_FLAG_RECONCILE_MS")
        if raw is None:
            return 45_000
        parsed = parse_js_number(raw)
        if not math.isfinite(parsed) or parsed < 5000:
            return 45_000
        return int(parsed)

    @property
    def feature_flag_admin_emails(self) -> list[str]:
        raw = self._env.get("FEATURE_FLAG_ADMIN_EMAILS")
        if not raw:
            return []
        return [e.strip().lower() for e in raw.split(",") if e.strip()]

    @property
    def feature_flag_metrics(self) -> bool:
        return self._env.get("FEATURE_FLAG_METRICS") != "false"

    # Uploads, HTTP Client, GraphQL, Captcha, Slack, Conformance
    @property
    def upload_max_files(self) -> int:
        return parse_positive_int(self._env.get("UPLOAD_MAX_FILES"), 5)

    @property
    def upload_max_file_size(self) -> int:
        return parse_positive_int(self._env.get("UPLOAD_MAX_FILE_SIZE"), 5_000_000)

    @property
    def upload_max_total_size(self) -> int:
        return parse_positive_int(self._env.get("UPLOAD_MAX_TOTAL_SIZE"), 20_000_000)

    @property
    def upload_virus_scan(self) -> bool:
        return self._env.get("UPLOAD_VIRUS_SCAN") == "true"

    @property
    def clamav_host(self) -> str:
        raw = self._env.get("CLAMAV_HOST")
        return raw.strip() if raw is not None and raw.strip() else "clamav"

    @property
    def clamav_port(self) -> int:
        return parse_positive_int(self._env.get("CLAMAV_PORT"), 3310)

    @property
    def http_client_allow_private_urls(self) -> bool:
        return self._env.get("HTTP_CLIENT_ALLOW_PRIVATE_URLS") == "true"

    @property
    def http_client_instance_count(self) -> int:
        return parse_http_client_instance_count(self._env)

    @property
    def hcaptcha_site_key(self) -> str | None:
        return self._env.get("HCAPTCHA_SITE_KEY")

    @property
    def hcaptcha_secret(self) -> str:
        return self._env["HCAPTCHA_SECRET"]

    @property
    def hcaptcha_verify_base_url(self) -> str:
        raw = self._env.get("HCAPTCHA_VERIFY_BASE_URL")
        return (
            raw.strip()
            if raw is not None and raw.strip()
            else "https://api.hcaptcha.com"
        )

    @property
    def slack_webhook_url(self) -> str | None:
        raw = self._env.get("SLACK_WEBHOOK_URL")
        return raw.strip() if raw is not None and raw.strip() else None

    @property
    def gql_max_complexity(self) -> int:
        return parse_positive_int(self._env.get("GQL_MAX_COMPLEXITY"), 50)

    @property
    def gql_user_budget(self) -> int:
        return parse_positive_int(self._env.get("GQL_USER_BUDGET"), 1000)

    @property
    def gql_max_concurrency(self) -> int:
        return parse_positive_int(self._env.get("GQL_MAX_CONCURRENCY"), 3)

    @property
    def gql_concurrency_key_ttl_sec(self) -> int:
        return parse_positive_int(self._env.get("GQL_CONCURRENCY_KEY_TTL_SEC"), 300)

    @property
    def rate_limit_fallback_cooldown_ms(self) -> int:
        """Mirror JS Number(RATE_LIMIT_FALLBACK_COOLDOWN_MS ?? 5000) semantics."""
        raw = self._env.get("RATE_LIMIT_FALLBACK_COOLDOWN_MS")
        if raw is None:
            return 5000
        parsed = parse_js_number(raw)
        if not math.isfinite(parsed) or parsed < 0:
            return 0
        return int(parsed)

    @property
    def conformance_harness_enabled(self) -> bool:
        return is_conformance_harness_enabled(self._env)

    # Parser delegates
    @property
    def rate_limit_storage(self) -> RateLimitStorage:
        return resolve_rate_limit_storage_from_config(self)

    @property
    def cache_storage(self) -> CacheStorage:
        return resolve_cache_storage_from_config(self)

    @property
    def cache_module_options(self) -> CacheModuleOptions:
        return parse_cache_module_options(self)

    @property
    def http_server_timeouts(self) -> HttpServerTimeouts:
        return parse_http_server_timeouts(self._env)

    @property
    def trust_proxy(self) -> bool | list[str]:
        return parse_trust_proxy(self._env.get("TRUST_PROXY"))

    @property
    def under_pressure(self) -> UnderPressureConfig:
        return parse_under_pressure_config(self._env)

    @property
    def inbound_bulkhead(self) -> InboundBulkheadConfig:
        return parse_inbound_bulkhead_config(self._env)

    @property
    def priority_shedding(self) -> PrioritySheddingConfig:
        return parse_priority_shedding_config(self._env)

    @property
    def inbound_load_level(self) -> InboundLoadLevelConfig:
        return parse_inbound_load_level_config(self._env)

    @property
    def websocket(self) -> WebSocketConfig:
        return parse_websocket_config(self._env)

    def get_background_jobs_config(self) -> BackgroundJobsConfig:
        return parse_background_jobs_config(self._env)

    @property
    def country_access(self) -> CountryAccessModuleOptions:
        return parse_country_access_options(self._env)

    @property
    def telemetry(self) -> TelemetryConfig:
        return parse_telemetry_config(self._env)

    @property
    def log_redaction(self) -> LogRedactionConfig:
        return parse_log_redaction_config(self._env)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached validated Settings loaded from process environment."""
    return Settings.from_env()
