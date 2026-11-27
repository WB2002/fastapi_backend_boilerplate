import re
from pathlib import Path
from types import MappingProxyType

import pytest

from app.common.config import (
    ENV_SCHEMA,
    REQUIRED_ENV_KEYS,
    is_conformance_harness_enabled,
    validate_env,
)

REPO_ROOT = Path(__file__).resolve().parents[4]

EXPECTED_REQUIRED_KEYS = (
    "JWT_PRIVATE_KEY",
    "JWT_PUBLIC_KEY",
    "COOKIE_SECRET",
    "ACCESS_TOKEN_TTL",
    "SESSION_TTL",
    "MONGO_URI",
    "REDIS_HOST",
    "REDIS_PORT",
    "GEOIP_DATABASE_PATH",
    "HCAPTCHA_SECRET",
)


def test_env_schema_has_exact_105_keys_in_order() -> None:
    assert len(ENV_SCHEMA) == 105
    assert sum(1 for field in ENV_SCHEMA.values() if field.required) == 10
    assert sum(1 for field in ENV_SCHEMA.values() if not field.required) == 95


def test_env_schema_exact_required_keys() -> None:
    assert REQUIRED_ENV_KEYS == EXPECTED_REQUIRED_KEYS


def test_returns_config_when_all_required_keys_present(
    valid_base_env: dict[str, str],
) -> None:
    config = {**valid_base_env, "PORT": "3000"}
    result = validate_env(config)
    assert result == config


def test_allows_optional_keys_omitted_empty_whitespace_or_none(
    valid_base_env: dict[str, str],
) -> None:
    config: dict[str, str | None] = {
        **valid_base_env,
        "FEATURE_FLAG_ADMIN_EMAILS": "",
        "ALLOWED_ORIGINS": "   ",
        "SLACK_WEBHOOK_URL": None,
    }
    result = validate_env(config)
    assert result["FEATURE_FLAG_ADMIN_EMAILS"] == ""
    assert result["ALLOWED_ORIGINS"] == "   "
    assert "SLACK_WEBHOOK_URL" not in result


def test_raises_when_single_required_key_missing(
    valid_base_env: dict[str, str],
) -> None:
    del valid_base_env["MONGO_URI"]
    with pytest.raises(
        ValueError,
        match=r"^Missing required environment variables: MONGO_URI$",
    ):
        validate_env(valid_base_env)


def test_raises_listing_all_missing_required_keys_in_schema_order(
    valid_base_env: dict[str, str],
) -> None:
    del valid_base_env["HCAPTCHA_SECRET"]
    del valid_base_env["REDIS_HOST"]
    del valid_base_env["JWT_PRIVATE_KEY"]
    with pytest.raises(
        ValueError,
        match=(
            r"^Missing required environment variables: "
            r"JWT_PRIVATE_KEY, REDIS_HOST, HCAPTCHA_SECRET$"
        ),
    ):
        validate_env(valid_base_env)


def test_raises_listing_all_10_required_keys_when_empty_env() -> None:
    expected = ", ".join(EXPECTED_REQUIRED_KEYS)
    with pytest.raises(
        ValueError,
        match=rf"^Missing required environment variables: {expected}$",
    ):
        validate_env({})


def test_treats_none_as_missing_for_required_keys(
    valid_base_env: dict[str, str],
) -> None:
    config: dict[str, str | None] = {**valid_base_env, "COOKIE_SECRET": None}
    with pytest.raises(
        ValueError,
        match=r"^Missing required environment variables: COOKIE_SECRET$",
    ):
        validate_env(config)


def test_treats_empty_string_as_missing_for_required_keys(
    valid_base_env: dict[str, str],
) -> None:
    valid_base_env["COOKIE_SECRET"] = ""
    with pytest.raises(
        ValueError,
        match=r"^Missing required environment variables: COOKIE_SECRET$",
    ):
        validate_env(valid_base_env)


@pytest.mark.parametrize("blank", ["   ", "\t", "\n  \t", "\ufeff", " \ufeff "])
def test_treats_whitespace_only_as_missing_for_required_keys(
    valid_base_env: dict[str, str],
    blank: str,
) -> None:
    valid_base_env["SESSION_TTL"] = blank
    with pytest.raises(
        ValueError,
        match=r"^Missing required environment variables: SESSION_TTL$",
    ):
        validate_env(valid_base_env)


@pytest.mark.parametrize("key", EXPECTED_REQUIRED_KEYS)
def test_every_required_key_is_enforced_individually(
    valid_base_env: dict[str, str],
    key: str,
) -> None:
    del valid_base_env[key]
    with pytest.raises(
        ValueError,
        match=rf"^Missing required environment variables: {key}$",
    ):
        validate_env(valid_base_env)


def test_preserves_extra_unknown_keys_untouched(
    valid_base_env: dict[str, str],
) -> None:
    config = {**valid_base_env, "CUSTOM_FLAG": "1", "EXTRA_EMPTY": ""}
    result = validate_env(config)
    assert result["CUSTOM_FLAG"] == "1"
    assert result["EXTRA_EMPTY"] == ""


def test_does_not_mutate_input_mapping(valid_base_env: dict[str, str]) -> None:
    frozen = MappingProxyType(valid_base_env)
    result = validate_env(frozen)
    assert result == valid_base_env
    assert result is not valid_base_env


def test_rejects_conformance_harness_enabled_in_production(
    valid_base_env: dict[str, str],
) -> None:
    config = {
        **valid_base_env,
        "NODE_ENV": "production",
        "CONFORMANCE_HARNESS_ENABLED": "true",
    }
    with pytest.raises(
        ValueError,
        match=r"^CONFORMANCE_HARNESS_ENABLED cannot be true when NODE_ENV=production$",
    ):
        validate_env(config)


def test_missing_required_keys_takes_precedence_over_production_conformance_guard(
    valid_base_env: dict[str, str],
) -> None:
    del valid_base_env["MONGO_URI"]
    config = {
        **valid_base_env,
        "NODE_ENV": "production",
        "CONFORMANCE_HARNESS_ENABLED": "true",
    }
    with pytest.raises(
        ValueError,
        match=r"^Missing required environment variables: MONGO_URI$",
    ):
        validate_env(config)


@pytest.mark.parametrize("node_env", ["development", "test", "staging", None, ""])
def test_allows_conformance_harness_enabled_outside_production(
    valid_base_env: dict[str, str],
    node_env: str | None,
) -> None:
    config: dict[str, str | None] = {
        **valid_base_env,
        "NODE_ENV": node_env,
        "CONFORMANCE_HARNESS_ENABLED": "true",
    }
    result = validate_env(config)
    assert result["CONFORMANCE_HARNESS_ENABLED"] == "true"


@pytest.mark.parametrize(
    "flag_value",
    [None, "", "false", "TRUE", "1", "yes"],
)
def test_allows_production_when_conformance_harness_not_exact_true(
    valid_base_env: dict[str, str],
    flag_value: str | None,
) -> None:
    config: dict[str, str | None] = {
        **valid_base_env,
        "NODE_ENV": "production",
        "CONFORMANCE_HARNESS_ENABLED": flag_value,
    }
    result = validate_env(config)
    assert result["NODE_ENV"] == "production"


def test_allows_conformance_harness_true_when_node_env_not_exact_production(
    valid_base_env: dict[str, str],
) -> None:
    config = {
        **valid_base_env,
        "NODE_ENV": "PRODUCTION",
        "CONFORMANCE_HARNESS_ENABLED": "true",
    }
    result = validate_env(config)
    assert result["CONFORMANCE_HARNESS_ENABLED"] == "true"


def test_is_conformance_harness_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert is_conformance_harness_enabled({}) is False
    assert (
        is_conformance_harness_enabled({"CONFORMANCE_HARNESS_ENABLED": "false"})
        is False
    )
    assert (
        is_conformance_harness_enabled({"CONFORMANCE_HARNESS_ENABLED": "TRUE"}) is False
    )
    assert (
        is_conformance_harness_enabled({"CONFORMANCE_HARNESS_ENABLED": "true"}) is True
    )

    monkeypatch.setenv("CONFORMANCE_HARNESS_ENABLED", "true")
    assert is_conformance_harness_enabled() is True
    monkeypatch.delenv("CONFORMANCE_HARNESS_ENABLED")
    assert is_conformance_harness_enabled() is False


def test_env_example_documents_all_105_schema_keys() -> None:
    example_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented_keys: set[str] = set()
    for line in example_text.splitlines():
        match = re.match(r"^(?:#\s*)?([A-Z0-9_]+)=", line.strip())
        if match:
            documented_keys.add(match.group(1))
    assert documented_keys == set(ENV_SCHEMA.keys())
