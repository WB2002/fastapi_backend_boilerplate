from __future__ import annotations

import re
from typing import Final

from pydantic import ConfigDict, Field, field_validator

from app.common.sanitization.sanitization_pipe import (
    SanitizedBaseModel,
    skip_sanitization,
)

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def _validate_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("email must be a valid email address")
    candidate = value.strip()
    if not candidate or len(candidate) > 320 or _EMAIL_RE.match(candidate) is None:
        raise ValueError("email must be an email")
    return candidate


@skip_sanitization("password")
class LoginDto(SanitizedBaseModel):
    """DTO for POST /auth/login matching NestJS LoginDto."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    email: str
    password: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return _validate_email(value)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) == 0:
            raise ValueError("password should not be empty")
        return value


@skip_sanitization("password")
class RegisterDto(SanitizedBaseModel):
    """DTO for POST /auth/register matching NestJS RegisterDto."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    email: str
    password: str = Field(min_length=8)
    firstName: str | None = Field(default=None, alias="firstName")
    lastName: str | None = Field(default=None, alias="lastName")

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return _validate_email(value)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) < 8:
            raise ValueError("password must be longer than or equal to 8 characters")
        return value

    @property
    def first_name(self) -> str | None:
        return self.firstName

    @property
    def last_name(self) -> str | None:
        return self.lastName


__all__ = ["LoginDto", "RegisterDto"]
