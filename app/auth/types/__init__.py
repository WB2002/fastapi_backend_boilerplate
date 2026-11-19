from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from fastapi import Request

from app.auth.constants import UserRole


@dataclass(slots=True)
class AuthenticatedUser:
    """Authenticated user claims attached to `request.state.user`."""

    id: str
    email: str
    jti: str
    role: str = UserRole.USER

    @property
    def sub(self) -> str:
        return self.id

    @property
    def _id(self) -> str:
        return self.id

    def get(self, key: str, default: Any = None) -> Any:
        if key in ("id", "sub", "_id"):
            return self.id
        if key == "email":
            return self.email
        if key == "jti":
            return self.jti
        if key == "role":
            return self.role
        return default

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and key not in ("id", "sub", "_id", "email", "jti", "role"):
            raise KeyError(key)
        return val

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "email": self.email,
            "jti": self.jti,
            "role": str(self.role),
        }


class SessionTokenResult(TypedDict):
    accessToken: str
    sessionId: str


class AuthResponsePayload(TypedDict):
    accessToken: str
    sessionId: str
    csrfToken: str


AuthRequest = Request

__all__ = [
    "AuthRequest",
    "AuthResponsePayload",
    "AuthenticatedUser",
    "SessionTokenResult",
]
