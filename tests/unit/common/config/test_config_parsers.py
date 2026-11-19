import math

import pytest

from app.common.config import (
    REQUIRED_ENV_KEYS,
    ApiPriorityLevel,
    CacheStorage,
    RateLimitStorage,
    Settings,
    assert_websocket_production_origins,
    get_settings,
    is_app_metrics_enabled,
    is_background_jobs_enabled,
    is_websocket_enabled,
    normalize_pem,
    parse_background_jobs_config,
    parse_cache_module_options,
    parse_country_access_options,
    parse_csv_paths,
    parse_http_client_instance_count,
    parse_http_server_timeouts,
    parse_inbound_bulkhead_config,
    parse_inbound_load_level_config,
    parse_js_int,
    parse_js_number,
    parse_log_redaction_config,
    parse_positive_timeout_ms,
    parse_priority_shedding_config,
    parse_telemetry_config,
    parse_trust_proxy,
    parse_under_pressure_config,
    parse_websocket_config,
    resolve_timeout_ms,
)


def test_parse_cache_module_options_defaults_and_valid() -> None:
    empty = parse_cache_module_options({})
    assert empty.to_dict() == {}

    blank = parse_cache_module_options(
        {
            "CACHE_TTL_JITTER_RATIO": "   ",
            "CACHE_LOADER_LOCK_TTL_MS": "",
            "CACHE_LOADER_WAIT_MS": None,
        }
    )
    assert blank.to_dict() == {}

    parsed = parse_cache_module_options(
        {
            "CACHE_TTL_JITTER_RATIO": "0.15",
            "CACHE_LOADER_LOCK_TTL_MS": "5000",
            "CACHE_LOADER_WAIT_MS": " 250 ",
            "CACHE_NEGATIVE_SOFT_TTL_MS": "12.0",
            "CACHE_NEGATIVE_HARD_TTL_MS": "1e3",
        }
    )
    assert parsed.ttl_jitter_ratio == 0.15
    assert parsed.loader_lock_ttl_ms == 5000
    assert parsed.loader_wait_ms == 250
    assert parsed.default_negative_soft_ttl_ms == 12
    assert parsed.default_negative_hard_ttl_ms == 1000
    assert (
        parse_cache_module_options({"CACHE_TTL_JITTER_RATIO": "0"}).ttl_jitter_ratio
        == 0.0
    )
    assert (
        parse_cache_module_options({"CACHE_TTL_JITTER_RATIO": "1"}).ttl_jitter_ratio
        == 1.0
    )


@pytest.mark.parametrize(
    "bad_ratio",
    ["-0.1", "1.01", "2", "NaN", "Infinity", "abc", "0_5"],
)
def test_parse_cache_module_options_rejects_invalid_ratio(bad_ratio: str) -> None:
    with pytest.raises(
        ValueError,
        match=r"^CACHE_TTL_JITTER_RATIO must be a number between 0 and 1$",
    ):
        parse_cache_module_options({"CACHE_TTL_JITTER_RATIO": bad_ratio})


@pytest.mark.parametrize(
    "bad_int",
    ["0", "-5", "12.34", "30000abc", "1_000", "Infinity", "NaN"],
)
def test_parse_cache_module_options_rejects_invalid_integers(bad_int: str) -> None:
    with pytest.raises(
        ValueError,
        match=rf'^Invalid cache config integer: "{bad_int}"$',
    ):
        parse_cache_module_options({"CACHE_LOADER_LOCK_TTL_MS": bad_int})


def test_parse_http_server_timeouts_valid_and_fallbacks() -> None:
    assert (parse_http_server_timeouts({}).keep_alive_timeout_ms) == 65_000
    assert (parse_http_server_timeouts({}).headers_timeout_ms) == 66_000

    t1 = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": "30000"})
    assert (t1.keep_alive_timeout_ms, t1.headers_timeout_ms) == (30_000, 31_000)

    t2 = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": " 1000 "})
    assert (t2.keep_alive_timeout_ms, t2.headers_timeout_ms) == (1_000, 2_000)

    t3 = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": "12.0"})
    assert (t3.keep_alive_timeout_ms, t3.headers_timeout_ms) == (12, 1_012)

    for invalid in (
        "not-a-number",
        "-1000",
        "0",
        "",
        "   ",
        "12.34",
        "30000abc",
        "1_000",
        "Infinity",
    ):
        fb = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": invalid})
        assert (fb.keep_alive_timeout_ms, fb.headers_timeout_ms) == (65_000, 66_000)


def test_parse_trust_proxy() -> None:
    for falsy in (None, "", "   ", "false", "FALSE", "0", " 0 "):
        assert parse_trust_proxy(falsy) is False
    for truthy in ("true", "TRUE", "1", " 1 "):
        assert parse_trust_proxy(truthy) is True
    assert parse_trust_proxy("10.0.0.1, 10.0.0.0/8") == ["10.0.0.1", "10.0.0.0/8"]
    assert parse_trust_proxy("127.0.0.1, , ::1,") == ["127.0.0.1", "::1"]
    assert parse_trust_proxy(",") == []


def test_parse_under_pressure_config() -> None:
    default_cfg = parse_under_pressure_config({})
    assert default_cfg.enabled is False
    assert default_cfg.max_event_loop_delay_ms == 1000
    assert default_cfg.max_heap_used_bytes == 0
    assert default_cfg.max_rss_bytes == 0
    assert default_cfg.retry_after_sec == 5
    assert default_cfg.expose_status_route is False

    assert (
        parse_under_pressure_config({"UNDER_PRESSURE_ENABLED": "YES"}).enabled is True
    )
    assert (
        parse_under_pressure_config({"UNDER_PRESSURE_ENABLED": "NO"}).enabled is False
    )
    assert (
        parse_under_pressure_config({"UNDER_PRESSURE_ENABLED": "invalid"}).enabled
        is False
    )

    assert (
        parse_under_pressure_config({"OVERLOAD_RETRY_AFTER_SEC": "0"}).retry_after_sec
        == 1
    )
    assert (
        parse_under_pressure_config({"OVERLOAD_RETRY_AFTER_SEC": "-5"}).retry_after_sec
        == 5
    )
    assert (
        parse_under_pressure_config(
            {"OVERLOAD_RETRY_AFTER_SEC": "250ms"}
        ).retry_after_sec
        == 250
    )


def test_parse_inbound_bulkhead_and_load_level_configs() -> None:
    bh_def = parse_inbound_bulkhead_config({})
    assert (bh_def.enabled, bh_def.max_in_flight) == (False, 100)
    assert (
        parse_inbound_bulkhead_config({"INBOUND_MAX_IN_FLIGHT": "0"}).max_in_flight
        == 100
    )
    assert (
        parse_inbound_bulkhead_config({"INBOUND_MAX_IN_FLIGHT": "50"}).max_in_flight
        == 50
    )

    ll_def = parse_inbound_load_level_config({})
    assert (
        ll_def.enabled,
        ll_def.max_queue,
        ll_def.max_wait_ms,
        ll_def.poll_interval_ms,
    ) == (
        False,
        100,
        2000,
        25,
    )
    ll_custom = parse_inbound_load_level_config(
        {
            "INBOUND_LOAD_LEVELING_ENABLED": "1",
            "INBOUND_LOAD_LEVEL_MAX_QUEUE": "0",
            "INBOUND_LOAD_LEVEL_MAX_WAIT_MS": "-10",
            "INBOUND_LOAD_LEVEL_POLL_MS": "15",
        }
    )
    assert (
        ll_custom.enabled,
        ll_custom.max_queue,
        ll_custom.max_wait_ms,
        ll_custom.poll_interval_ms,
    ) == (
        True,
        100,
        2000,
        15,
    )


def test_parse_priority_shedding_config() -> None:
    cfg = parse_priority_shedding_config({})
    assert cfg.enabled is False
    assert cfg.default_priority == ApiPriorityLevel.NORMAL
    assert (
        cfg.thresholds.critical,
        cfg.thresholds.high,
        cfg.thresholds.normal,
        cfg.thresholds.low,
    ) == (
        1.0,
        0.95,
        0.85,
        0.7,
    )

    assert (
        parse_priority_shedding_config(
            {"INBOUND_DEFAULT_API_PRIORITY": "CRITICAL"}
        ).default_priority
        == ApiPriorityLevel.CRITICAL
    )
    assert (
        parse_priority_shedding_config(
            {"INBOUND_DEFAULT_API_PRIORITY": "high"}
        ).default_priority
        == ApiPriorityLevel.HIGH
    )
    assert (
        parse_priority_shedding_config(
            {"INBOUND_DEFAULT_API_PRIORITY": "low"}
        ).default_priority
        == ApiPriorityLevel.LOW
    )
    assert (
        parse_priority_shedding_config(
            {"INBOUND_DEFAULT_API_PRIORITY": "unknown"}
        ).default_priority
        == ApiPriorityLevel.NORMAL
    )

    custom = parse_priority_shedding_config(
        {
            "INBOUND_PRIORITY_SHED_CRITICAL_AT": "1",
            "INBOUND_PRIORITY_SHED_HIGH_AT": "0.9abc",
            "INBOUND_PRIORITY_SHED_NORMAL_AT": "0",
            "INBOUND_PRIORITY_SHED_LOW_AT": "1.01",
        }
    )
    assert custom.thresholds.critical == 1.0
    assert custom.thresholds.high == 0.9
    assert custom.thresholds.normal == 0.85
    assert custom.thresholds.low == 0.7


def test_parse_websocket_config_and_prod_origin_guard() -> None:
    dev = parse_websocket_config({})
    assert dev.enabled is True
    assert dev.path == "/socket.io"
    assert dev.allow_query_token is False
    assert dev.redis_adapter_enabled is False
    assert dev.cors_origins is True
    assert dev.max_connections_per_user == 5
    assert dev.max_inbound_buffer_bytes == 1_000_000
    assert dev.messages_per_minute == 120
    assert dev.connection_key_ttl_sec == 86_400

    # Variant C strict boolean semantics
    assert parse_websocket_config({"WEBSOCKET_ENABLED": "TRUE"}).enabled is False
    assert parse_websocket_config({"WEBSOCKET_ENABLED": "   "}).enabled is False
    assert parse_websocket_config({"WEBSOCKET_ENABLED": "1"}).enabled is True
    assert parse_websocket_config({"WEBSOCKET_ENABLED": ""}).enabled is True
    assert is_websocket_enabled({"WEBSOCKET_ENABLED": "TRUE"}) is False

    prod = parse_websocket_config(
        {
            "NODE_ENV": "production",
            "ALLOWED_ORIGINS": "https://a.com, https://b.com",
        }
    )
    assert prod.redis_adapter_enabled is True
    assert prod.cors_origins == ["https://a.com", "https://b.com"]
    assert_websocket_production_origins(prod, "production")

    prod_no_origins = parse_websocket_config({"NODE_ENV": "production"})
    with pytest.raises(
        ValueError,
        match=r"^ALLOWED_ORIGINS is required for WebSocket when NODE_ENV=production$",
    ):
        assert_websocket_production_origins(prod_no_origins, "production")

    prod_disabled = parse_websocket_config(
        {"NODE_ENV": "production", "WEBSOCKET_ENABLED": "false"}
    )
    assert_websocket_production_origins(prod_disabled, "production")

    prod_no_adapter = parse_websocket_config(
        {"NODE_ENV": "production", "WEBSOCKET_REDIS_ADAPTER": "false"}
    )
    assert prod_no_adapter.redis_adapter_enabled is False


def test_parse_background_jobs_config() -> None:
    disabled = parse_background_jobs_config({})
    assert disabled.enabled is False
    assert disabled.worker_enabled is False
    assert disabled.default_queue_name == "default"
    assert disabled.claim_lease_sec == 300
    assert disabled.worker_concurrency == 5
    assert disabled.lease_reclaim_enabled is True
    assert disabled.lease_reclaim_interval_ms == 30_000
    assert disabled.lease_reclaim_batch_size == 100
    assert disabled.orphan_min_age_sec == 30

    with pytest.raises(
        ValueError,
        match=r"^POSTGRES_DB is required when BACKGROUND_JOBS_ENABLED=true$",
    ):
        parse_background_jobs_config(
            {"BACKGROUND_JOBS_ENABLED": "true", "POSTGRES_DB": "   "}
        )

    assert (
        parse_background_jobs_config({"BACKGROUND_JOBS_ENABLED": "TRUE"}).enabled
        is False
    )
    assert (
        parse_background_jobs_config(
            {"BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED": "FALSE"}
        ).lease_reclaim_enabled
        is True
    )
    assert (
        parse_background_jobs_config(
            {"BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED": "false"}
        ).lease_reclaim_enabled
        is False
    )
    pw_cfg = parse_background_jobs_config({"POSTGRES_PASSWORD": "  secret  "})
    assert pw_cfg.postgres.password == "  secret  "
    assert is_background_jobs_enabled({"BACKGROUND_JOBS_ENABLED": "true"}) is True


def test_parse_country_access_options() -> None:
    with pytest.raises(ValueError, match=r"^GEOIP_DATABASE_PATH is required$"):
        parse_country_access_options({})

    defaults = parse_country_access_options({"GEOIP_DATABASE_PATH": "/data/Geo.mmdb"})
    assert defaults.refresh_interval_ms == 60_000
    assert defaults.unknown_country_policy == "block"
    assert defaults.bypass_paths == ["/health", "/health/ready"]
    assert defaults.persistence_provider == "postgres"
    assert defaults.postgres.database == "nestjs_db"
    assert defaults.postgres.port == 5432

    custom = parse_country_access_options(
        {
            "GEOIP_DATABASE_PATH": "/data/Geo.mmdb",
            "COUNTRY_ACCESS_BYPASS_PATHS": "healthz, readyz, /metrics",
            "COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY": "ALLOW",
        }
    )
    assert custom.bypass_paths == ["/healthz", "/readyz", "/metrics"]
    assert custom.unknown_country_policy == "allow"

    for bad_policy in ("maybe", ""):
        with pytest.raises(
            ValueError,
            match=r"COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY",
        ):
            parse_country_access_options(
                {
                    "GEOIP_DATABASE_PATH": "/data/Geo.mmdb",
                    "COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY": bad_policy,
                }
            )

    for bad_int in ("0", "-1", "abc"):
        with pytest.raises(
            ValueError,
            match=r"Invalid positive integer configuration value",
        ):
            parse_country_access_options(
                {
                    "GEOIP_DATABASE_PATH": "/data/Geo.mmdb",
                    "COUNTRY_ACCESS_REFRESH_INTERVAL_MS": bad_int,
                }
            )


def test_parse_telemetry_and_redaction_and_helpers() -> None:
    tel = parse_telemetry_config({})
    assert tel.otel_enabled is False
    assert tel.service_name == "nestjs-boilerplate"
    assert tel.app_metrics_enabled is True
    assert tel.otlp_endpoint is None
    assert is_app_metrics_enabled(tel) is True

    tel_custom = parse_telemetry_config(
        {
            "OTEL_ENABLED": "yes",
            "APP_METRICS_ENABLED": "0",
            "OTEL_SERVICE_NAME": "   ",
            "OTEL_EXPORTER_OTLP_ENDPOINT": " http://localhost:4318 ",
        }
    )
    # Variant B ignores "yes" -> falls back to default (False)
    assert tel_custom.otel_enabled is False
    assert tel_custom.app_metrics_enabled is False
    assert tel_custom.service_name == "nestjs-boilerplate"
    assert tel_custom.otlp_endpoint == "http://localhost:4318"

    assert parse_log_redaction_config({}).enabled is True
    assert (
        parse_log_redaction_config({"LOG_REDACTION_ENABLED": "FALSE"}).enabled is True
    )
    assert (
        parse_log_redaction_config({"LOG_REDACTION_ENABLED": "false"}).enabled is False
    )

    assert parse_http_client_instance_count({}) == 1
    assert parse_http_client_instance_count("4") == 4
    assert parse_http_client_instance_count("0") == 1
    assert parse_csv_paths(" /a , /b ", ("/health",)) == ["/health", "/a", "/b"]
    assert parse_positive_timeout_ms("12.9", 30_000) == 12
    assert parse_positive_timeout_ms("1_000", 30_000) == 30_000
    assert (
        resolve_timeout_ms(
            header_ms=5000,
            default_timeout_ms=30_000,
            max_timeout_ms=120_000,
        ).timeout_ms
        == 5000
    )


def test_settings_from_env_and_get_settings(
    valid_base_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.from_env(valid_base_env)
    assert settings.port == 3000
    assert settings.node_env == "development"
    assert settings.rate_limit_storage == RateLimitStorage.MEMORY
    assert settings.cache_storage == CacheStorage.MEMORY
    assert settings.http_server_timeouts.keep_alive_timeout_ms == 65_000
    assert settings.trust_proxy is False
    assert settings.telemetry.service_name == "nestjs-boilerplate"
    assert settings.get_background_jobs_config().enabled is False
    assert settings.country_access.postgres.database == "nestjs_db"

    for k, v in valid_base_env.items():
        monkeypatch.setenv(k, v)
    cached_a = get_settings()
    cached_b = get_settings()
    assert cached_a is cached_b


def test_parse_js_number_ecmascript_parity() -> None:
    assert parse_js_number("") == 0.0
    assert parse_js_number("   ") == 0.0
    assert parse_js_number("\ufeff") == 0.0
    assert parse_js_number(" \ufeff 42 \ufeff ") == 42.0
    assert parse_js_number("1e4") == 10_000.0
    assert parse_js_number("0x10") == 16.0
    assert parse_js_number("0XFF") == 255.0
    assert parse_js_number("0b10") == 2.0
    assert parse_js_number("0o10") == 8.0
    assert parse_js_number("Infinity") == math.inf
    assert parse_js_number("+Infinity") == math.inf
    assert parse_js_number("-Infinity") == -math.inf
    assert parse_js_number("9" * 5000) == math.inf

    for non_num in (
        "\u0661\u0662",
        "\u0660.\u0665",
        "\u0661\u0660\u0660\u0660\u0660",
        "\x85",
        "+0x10",
        "-0x10",
        "-0b10",
        "-0o10",
        "1_000",
        "inf",
        "+inf",
        "-inf",
        "infinity",
        "NaN",
        "nan",
        "30000abc",
    ):
        assert math.isnan(parse_js_number(non_num)), f"Expected NaN for {non_num!r}"


def test_js_number_radix_unicode_and_bom_in_cache_http_and_timeout_parsers() -> None:
    # Radix literals accepted, Arabic-Indic rejected, BOM treated as unset
    cache_radix = parse_cache_module_options(
        {
            "CACHE_TTL_JITTER_RATIO": "0.5",
            "CACHE_LOADER_LOCK_TTL_MS": "0x10",
            "CACHE_LOADER_WAIT_MS": "0b10",
            "CACHE_NEGATIVE_SOFT_TTL_MS": "0o10",
            "CACHE_NEGATIVE_HARD_TTL_MS": "1e4",
        }
    )
    assert cache_radix.loader_lock_ttl_ms == 16
    assert cache_radix.loader_wait_ms == 2
    assert cache_radix.default_negative_soft_ttl_ms == 8
    assert cache_radix.default_negative_hard_ttl_ms == 10_000
    assert (
        parse_cache_module_options({"CACHE_LOADER_LOCK_TTL_MS": "\ufeff"}).to_dict()
        == {}
    )

    for bad_ratio in ("\u0660.\u0665", "inf", "nan"):
        with pytest.raises(
            ValueError,
            match=r"^CACHE_TTL_JITTER_RATIO must be a number between 0 and 1$",
        ):
            parse_cache_module_options({"CACHE_TTL_JITTER_RATIO": bad_ratio})

    for bad_int in ("\u0661\u0662", "\u0661\u0660\u0660\u0660\u0660", "+0x10", "-0x10"):
        with pytest.raises(ValueError, match=r"^Invalid cache config integer:"):
            parse_cache_module_options({"CACHE_LOADER_LOCK_TTL_MS": bad_int})

    # HTTP server timeouts
    assert (
        parse_http_server_timeouts(
            {"HTTP_KEEP_ALIVE_TIMEOUT_MS": "0x10"}
        ).keep_alive_timeout_ms
        == 16
    )
    assert (
        parse_http_server_timeouts(
            {"HTTP_KEEP_ALIVE_TIMEOUT_MS": "0b10"}
        ).keep_alive_timeout_ms
        == 2
    )
    assert (
        parse_http_server_timeouts(
            {"HTTP_KEEP_ALIVE_TIMEOUT_MS": "0o10"}
        ).keep_alive_timeout_ms
        == 8
    )
    assert (
        parse_http_server_timeouts(
            {"HTTP_KEEP_ALIVE_TIMEOUT_MS": "1e4"}
        ).keep_alive_timeout_ms
        == 10_000
    )
    for invalid_timeout in (
        "\u0661\u0662",
        "\u0661\u0660\u0660\u0660\u0660",
        "\ufeff",
        "+0x10",
        "inf",
    ):
        t = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": invalid_timeout})
        assert (t.keep_alive_timeout_ms, t.headers_timeout_ms) == (65_000, 66_000)

    # parse_positive_timeout_ms
    assert parse_positive_timeout_ms("0x10", 30_000) == 16
    assert parse_positive_timeout_ms("0b10", 30_000) == 2
    assert parse_positive_timeout_ms("0o10", 30_000) == 8
    assert parse_positive_timeout_ms("1e4", 30_000) == 10_000
    for invalid_pto in (
        "\u0661\u0662",
        "\u0660.\u0665",
        "\u0661\u0660\u0660\u0660\u0660",
        "\ufeff",
        "+0x10",
        "inf",
    ):
        assert parse_positive_timeout_ms(invalid_pto, 30_000) == 30_000


def test_parse_js_int_handles_over_4300_digits() -> None:
    huge = "9" * 5000
    assert parse_js_int(huge) is None
    assert (
        parse_inbound_bulkhead_config({"INBOUND_MAX_IN_FLIGHT": huge}).max_in_flight
        == 100
    )
    assert (
        parse_under_pressure_config({"OVERLOAD_RETRY_AFTER_SEC": huge}).retry_after_sec
        == 5
    )


def test_normalize_pem_and_settings_edge_properties() -> None:
    assert normalize_pem("line1\\nline2\\n") == "line1\nline2\n"
    assert normalize_pem("line1\nline2") == "line1\nline2"

    base = {k: "val" for k in REQUIRED_ENV_KEYS}

    # feature_flag_reconcile_ms
    assert Settings.from_env(base).feature_flag_reconcile_ms == 45_000
    assert (
        Settings.from_env(
            {**base, "FEATURE_FLAG_RECONCILE_MS": "4999"}
        ).feature_flag_reconcile_ms
        == 45_000
    )
    assert (
        Settings.from_env(
            {**base, "FEATURE_FLAG_RECONCILE_MS": "5000"}
        ).feature_flag_reconcile_ms
        == 5_000
    )
    assert (
        Settings.from_env(
            {**base, "FEATURE_FLAG_RECONCILE_MS": "1e4"}
        ).feature_flag_reconcile_ms
        == 10_000
    )
    assert (
        Settings.from_env(
            {**base, "FEATURE_FLAG_RECONCILE_MS": "0x2000"}
        ).feature_flag_reconcile_ms
        == 8_192
    )
    assert (
        Settings.from_env(
            {**base, "FEATURE_FLAG_RECONCILE_MS": "\u0661\u0660\u0660\u0660\u0660"}
        ).feature_flag_reconcile_ms
        == 45_000
    )
    assert (
        Settings.from_env(
            {**base, "FEATURE_FLAG_RECONCILE_MS": "\ufeff"}
        ).feature_flag_reconcile_ms
        == 45_000
    )
    assert (
        Settings.from_env(
            {**base, "FEATURE_FLAG_RECONCILE_MS": "inf"}
        ).feature_flag_reconcile_ms
        == 45_000
    )

    # request_timeout_max_ms clamping
    clamped = Settings.from_env(
        {
            **base,
            "REQUEST_TIMEOUT_MS": "60000",
            "REQUEST_TIMEOUT_MAX_MS": "30000",
        }
    )
    assert clamped.request_timeout_ms == 60_000
    assert clamped.request_timeout_max_ms == 60_000
    hex_max = Settings.from_env(
        {
            **base,
            "REQUEST_TIMEOUT_MS": "10000",
            "REQUEST_TIMEOUT_MAX_MS": "0x20000",
        }
    )
    assert hex_max.request_timeout_max_ms == 131_072

    # rate_limit_fallback_cooldown_ms
    assert Settings.from_env(base).rate_limit_fallback_cooldown_ms == 5_000
    assert (
        Settings.from_env(
            {**base, "RATE_LIMIT_FALLBACK_COOLDOWN_MS": "1e4"}
        ).rate_limit_fallback_cooldown_ms
        == 10_000
    )
    assert (
        Settings.from_env(
            {**base, "RATE_LIMIT_FALLBACK_COOLDOWN_MS": "0x10"}
        ).rate_limit_fallback_cooldown_ms
        == 16
    )
    assert (
        Settings.from_env(
            {**base, "RATE_LIMIT_FALLBACK_COOLDOWN_MS": "0b10"}
        ).rate_limit_fallback_cooldown_ms
        == 2
    )
    assert (
        Settings.from_env(
            {**base, "RATE_LIMIT_FALLBACK_COOLDOWN_MS": "0o10"}
        ).rate_limit_fallback_cooldown_ms
        == 8
    )
    for zero_equiv in (
        "",
        "   ",
        "\ufeff",
        "0",
        "-5",
        "abc",
        "NaN",
        "\u0661\u0662",
        "Infinity",
    ):
        s = Settings.from_env({**base, "RATE_LIMIT_FALLBACK_COOLDOWN_MS": zero_equiv})
        assert s.rate_limit_fallback_cooldown_ms == 0
