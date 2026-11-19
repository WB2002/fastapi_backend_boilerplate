import os
from collections.abc import Mapping

from app.common.config.env_schema import ENV_SCHEMA
from app.common.config.parsers import trim_js


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and trim_js(value) == "":
        return True
    return False


def validate_env(raw_env: Mapping[str, str | None]) -> dict[str, str]:
    """Validate required env vars in schema order and guard production conformance."""
    missing: list[str] = []
    for key, field in ENV_SCHEMA.items():
        if not field.required:
            continue
        if _is_missing(raw_env.get(key)):
            missing.append(key)

    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    if (
        raw_env.get("NODE_ENV") == "production"
        and raw_env.get("CONFORMANCE_HARNESS_ENABLED") == "true"
    ):
        raise ValueError(
            "CONFORMANCE_HARNESS_ENABLED cannot be true when NODE_ENV=production"
        )

    return {key: value for key, value in raw_env.items() if value is not None}


def is_conformance_harness_enabled(
    env: Mapping[str, str | None] | None = None,
) -> bool:
    """Return True only when CONFORMANCE_HARNESS_ENABLED is exactly 'true'."""
    target = os.environ if env is None else env
    return target.get("CONFORMANCE_HARNESS_ENABLED") == "true"
