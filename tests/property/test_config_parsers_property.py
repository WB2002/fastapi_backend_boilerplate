import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.common.config import (
    ENV_SCHEMA,
    REQUIRED_ENV_KEYS,
    CacheStorage,
    RateLimitStorage,
    parse_cache_module_options,
    parse_country_access_options,
    parse_http_server_timeouts,
    parse_inbound_bulkhead_config,
    parse_inbound_load_level_config,
    parse_js_int,
    parse_js_number,
    parse_priority_shedding_config,
    parse_trust_proxy,
    parse_under_pressure_config,
    resolve_cache_storage,
    resolve_rate_limit_storage,
    validate_env,
)
from app.common.config.parsers import trim_js


@given(raw=st.one_of(st.none(), st.text()))
def test_parse_http_server_timeouts_never_raises_and_maintains_offset(
    raw: str | None,
) -> None:
    result = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": raw})
    assert result.keep_alive_timeout_ms >= 1
    assert result.headers_timeout_ms == result.keep_alive_timeout_ms + 1000


@given(n=st.integers(min_value=1, max_value=10**9))
def test_parse_http_server_timeouts_accepts_positive_ints(n: int) -> None:
    result = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": f" {n} "})
    assert result.keep_alive_timeout_ms == n
    assert result.headers_timeout_ms == n + 1000


@given(n=st.integers(max_value=0))
def test_parse_http_server_timeouts_non_positive_falls_back(n: int) -> None:
    result = parse_http_server_timeouts({"HTTP_KEEP_ALIVE_TIMEOUT_MS": str(n)})
    assert result.keep_alive_timeout_ms == 65_000
    assert result.headers_timeout_ms == 66_000


@given(raw=st.one_of(st.none(), st.text()))
def test_parse_trust_proxy_never_raises_and_cleans_list(raw: str | None) -> None:
    result = parse_trust_proxy(raw)
    assert isinstance(result, (bool, list))
    if isinstance(result, list):
        assert all(
            isinstance(item, str) and len(item) > 0 and item == item.strip()
            for item in result
        )


@given(
    node_env=st.text(),
    blank=st.one_of(st.none(), st.from_regex(r"[ \t\n]*", fullmatch=True)),
)
def test_coordination_storage_defaults_by_node_env(
    node_env: str,
    blank: str | None,
) -> None:
    rl = resolve_rate_limit_storage(node_env=node_env, rate_limit_storage=blank)
    cs = resolve_cache_storage(node_env=node_env, cache_storage=blank)
    if node_env == "production":
        assert rl == RateLimitStorage.REDIS
        assert cs == CacheStorage.REDIS
    else:
        assert rl == RateLimitStorage.MEMORY
        assert cs == CacheStorage.MEMORY


@given(raw=st.text().filter(lambda s: s.strip().lower() not in ("", "memory", "redis")))
def test_coordination_storage_rejects_unknown_tokens(raw: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_rate_limit_storage(node_env="development", rate_limit_storage=raw)
    assert "RATE_LIMIT_STORAGE" in str(exc_info.value)
    assert raw in str(exc_info.value)


@given(
    ratio=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
)
def test_parse_cache_module_options_valid_ratio(ratio: float) -> None:
    opts = parse_cache_module_options({"CACHE_TTL_JITTER_RATIO": repr(ratio)})
    assert opts.ttl_jitter_ratio is not None
    assert 0.0 <= opts.ttl_jitter_ratio <= 1.0


@given(bad=st.floats().filter(lambda x: not math.isfinite(x) or x < 0.0 or x > 1.0))
def test_parse_cache_module_options_rejects_out_of_range_ratio(bad: float) -> None:
    with pytest.raises(
        ValueError,
        match=r"^CACHE_TTL_JITTER_RATIO must be a number between 0 and 1$",
    ):
        parse_cache_module_options({"CACHE_TTL_JITTER_RATIO": repr(bad)})


@given(n=st.integers(min_value=1, max_value=10**9))
def test_parse_cache_module_options_valid_positive_int(n: int) -> None:
    opts = parse_cache_module_options({"CACHE_LOADER_LOCK_TTL_MS": str(n)})
    assert opts.loader_lock_ttl_ms == n


@given(n=st.integers(max_value=0))
def test_parse_cache_module_options_rejects_non_positive_int(n: int) -> None:
    with pytest.raises(ValueError, match=r"Invalid cache config integer"):
        parse_cache_module_options({"CACHE_LOADER_LOCK_TTL_MS": str(n)})


@given(
    env=st.dictionaries(
        keys=st.sampled_from(
            [
                "UNDER_PRESSURE_ENABLED",
                "UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS",
                "UNDER_PRESSURE_MAX_HEAP_USED_BYTES",
                "UNDER_PRESSURE_MAX_RSS_BYTES",
                "UNDER_PRESSURE_EXPOSE_STATUS_ROUTE",
                "OVERLOAD_RETRY_AFTER_SEC",
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
            ]
        ),
        values=st.text(),
    )
)
def test_overload_parsers_never_raise_and_preserve_invariants(
    env: dict[str, str],
) -> None:
    up = parse_under_pressure_config(env)
    assert up.retry_after_sec >= 1
    assert up.max_event_loop_delay_ms >= 0
    assert up.max_heap_used_bytes >= 0
    assert up.max_rss_bytes >= 0

    bh = parse_inbound_bulkhead_config(env)
    assert bh.max_in_flight >= 1

    ps = parse_priority_shedding_config(env)
    for t in (
        ps.thresholds.critical,
        ps.thresholds.high,
        ps.thresholds.normal,
        ps.thresholds.low,
    ):
        assert math.isfinite(t)
        assert 0.0 < t <= 1.0

    ll = parse_inbound_load_level_config(env)
    assert ll.max_queue >= 1
    assert ll.max_wait_ms >= 1
    assert ll.poll_interval_ms >= 1


@given(bypass_csv=st.text())
def test_country_access_bypass_paths_normalized(bypass_csv: str) -> None:
    opts = parse_country_access_options(
        {
            "GEOIP_DATABASE_PATH": "/tmp/Geo.mmdb",
            "COUNTRY_ACCESS_BYPASS_PATHS": bypass_csv,
        }
    )
    for path in opts.bypass_paths:
        assert path.startswith("/")
        assert path == path.strip()
        assert len(path) >= 1


@given(
    missing_subset=st.sets(st.sampled_from(REQUIRED_ENV_KEYS), min_size=1),
    extras=st.dictionaries(
        keys=st.text(min_size=1).filter(lambda k: k not in ENV_SCHEMA),
        values=st.text(),
    ),
)
def test_validate_env_lists_all_missing_in_schema_order(
    missing_subset: set[str],
    extras: dict[str, str],
) -> None:
    base = {k: "val" for k in REQUIRED_ENV_KEYS if k not in missing_subset}
    base.update(extras)

    expected_order = [k for k in REQUIRED_ENV_KEYS if k in missing_subset]
    with pytest.raises(ValueError) as exc_info:
        validate_env(base)
    assert str(exc_info.value) == (
        f"Missing required environment variables: {', '.join(expected_order)}"
    )


@given(
    n=st.integers(min_value=0, max_value=10**9),
    pad=st.sampled_from(["", " ", "\ufeff", " \ufeff\t"]),
)
def test_parse_js_number_radix_and_bom_round_trip(n: int, pad: str) -> None:
    for lit in (str(n), hex(n), hex(n).upper(), bin(n), oct(n)):
        assert parse_js_number(f"{pad}{lit}{pad}") == float(n)
    for signed_radix in (f"+{hex(n)}", f"-{hex(n)}", f"+{bin(n)}", f"-{oct(n)}"):
        assert math.isnan(parse_js_number(signed_radix))


@given(raw=st.text(min_size=1).filter(lambda s: not s.isascii() and trim_js(s) != ""))
def test_parse_js_number_rejects_non_ascii_as_nan(raw: str) -> None:
    assert math.isnan(parse_js_number(raw))


@given(length=st.integers(min_value=4301, max_value=5500))
def test_parse_js_int_never_raises_on_long_digits(length: int) -> None:
    assert parse_js_int("9" * length) is None


@given(
    key=st.sampled_from(REQUIRED_ENV_KEYS),
    bom_ws=st.sampled_from(["\ufeff", " \ufeff ", "\t\ufeff\n"]),
)
def test_validate_env_rejects_bom_whitespace_for_required_keys(
    key: str,
    bom_ws: str,
) -> None:
    env = {k: "val" for k in REQUIRED_ENV_KEYS}
    env[key] = bom_ws
    with pytest.raises(ValueError) as exc_info:
        validate_env(env)
    assert str(exc_info.value) == f"Missing required environment variables: {key}"
