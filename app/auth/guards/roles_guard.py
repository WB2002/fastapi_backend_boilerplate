from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Final

from fastapi import HTTPException, Request

from app.auth.constants import UserRole

ROLES_KEY: Final[str] = "roles"
WS_ROLES_KEY: Final[str] = "wsRoles"


class ForbiddenException(HTTPException):
    """NestJS-compatible 403 ForbiddenException."""

    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(status_code=403, detail=message)
        self.message = message
        self.response = {
            "statusCode": 403,
            "message": message,
            "error": "Forbidden",
        }

    def get_status(self) -> int:
        return 403

    def getStatus(self) -> int:
        return 403

    def get_response(self) -> dict[str, Any]:
        return dict(self.response)

    def getResponse(self) -> dict[str, Any]:
        return self.get_response()


def roles(*required_roles: UserRole | str) -> Callable[[Any], Any]:
    """Decorator attaching required UserRole list to a handler or controller."""
    normalized = tuple(
        r.value if isinstance(r, UserRole) else str(r) for r in required_roles
    )

    def _decorator(target: Any) -> Any:
        setattr(target, ROLES_KEY, normalized)
        return target

    return _decorator


def extract_user_role(request_or_user: Any) -> str | None:
    if request_or_user is None:
        return None
    user = getattr(request_or_user, "user", None)
    if user is None:
        state = getattr(request_or_user, "state", None)
        user = getattr(state, "user", None) if state is not None else None
    if user is None and isinstance(request_or_user, dict):
        user = request_or_user.get("user")
    if user is None:
        user = request_or_user

    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    if isinstance(role, UserRole):
        return role.value
    return str(role) if isinstance(role, str) and role else None


def check_user_role(
    request_or_user: Any,
    required_roles: Sequence[UserRole | str],
    *,
    error_message: str = "Insufficient role",
) -> bool:
    if not required_roles:
        return True
    role = extract_user_role(request_or_user)
    allowed_set = {
        r.value if isinstance(r, UserRole) else str(r) for r in required_roles
    }
    if role is None or role not in allowed_set:
        raise ForbiddenException(error_message)
    return True


class RolesGuard:
    """Role verification guard for required `UserRole` metadata."""

    def __init__(
        self,
        required_roles: Sequence[UserRole | str] | None = None,
        *,
        error_message: str = "Insufficient role",
    ) -> None:
        self._required_roles = tuple(required_roles) if required_roles else ()
        self._error_message = error_message

    def can_activate(
        self,
        request: Request | Any,
        *,
        handler: Any = None,
        controller: Any = None,
    ) -> bool:
        required = list(self._required_roles)
        if not required and handler is not None:
            required = list(getattr(handler, ROLES_KEY, ()) or ())
        if not required and controller is not None:
            required = list(getattr(controller, ROLES_KEY, ()) or ())
        if not required:
            scope = getattr(request, "scope", None)
            ep = scope.get("endpoint") if isinstance(scope, dict) else None
            if ep is not None:
                required = list(getattr(ep, ROLES_KEY, ()) or ())
        return check_user_role(
            request,
            required,
            error_message=self._error_message,
        )

    def canActivate(
        self,
        request: Request | Any,
        *,
        handler: Any = None,
        controller: Any = None,
    ) -> bool:
        return self.can_activate(
            request,
            handler=handler,
            controller=controller,
        )

    async def __call__(self, request: Request) -> bool:
        return self.can_activate(request)


Roles = roles

__all__ = [
    "ROLES_KEY",
    "WS_ROLES_KEY",
    "ForbiddenException",
    "Roles",
    "RolesGuard",
    "check_user_role",
    "extract_user_role",
    "roles",
]
