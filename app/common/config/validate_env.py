import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from app.common.config.env_schema import ENV_SCHEMA
from app.common.config.parsers import trim_js


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and trim_js(value) == "":
        return True
    return False


class EnvValidator(BaseModel):
    """Pydantic model validating required env vars and production guards."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    raw_env: Mapping[str, str | None]

    @model_validator(mode="after")
    def validate_schema(self) -> "EnvValidator":
        missing: list[str] = []
        for key, field in ENV_SCHEMA.items():
            if not field.required:
                continue
            if _is_missing(self.raw_env.get(key)):
                missing.append(key)

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        if (
            self.raw_env.get("NODE_ENV") == "production"
            and self.raw_env.get("CONFORMANCE_HARNESS_ENABLED") == "true"
        ):
            raise ValueError(
                "CONFORMANCE_HARNESS_ENABLED cannot be true when NODE_ENV=production"
            )
        return self


def validate_env(raw_env: Mapping[str, str | None]) -> dict[str, str]:
    """Validate required env vars in schema order and guard production conformance."""
    try:
        EnvValidator(raw_env=raw_env)
    except ValidationError as err:
        for error in err.errors():
            msg = error.get("msg", "")
            prefix = "Value error, "
            if msg.startswith(prefix):
                raise ValueError(msg[len(prefix) :]) from None
            raise ValueError(msg) from None

    return {key: value for key, value in raw_env.items() if value is not None}


def is_conformance_harness_enabled(
    env: Mapping[str, str | None] | None = None,
) -> bool:
    """Return True only when CONFORMANCE_HARNESS_ENABLED is exactly 'true'."""
    target = os.environ if env is None else env
    return target.get("CONFORMANCE_HARNESS_ENABLED") == "true"
