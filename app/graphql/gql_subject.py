from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.common.constants import DEFAULT_GUEST_COOKIE_NAME
from app.common.utils.object_utils import read_cookie_field

_ANON_SUBJECT: str = "anon"


def resolve_gql_subject(
    *,
    user_id: str | None = None,
    guest_id: str | None = None,
    cookies: Mapping[str, Any] | None = None,
    scope_state: Mapping[str, Any] | None = None,
) -> str:
    """Return a stable Redis subject key: ``user:``, ``guest:``, or ``anon``."""
    if user_id is not None and str(user_id).strip():
        return f"user:{user_id.strip()}"
    if guest_id is not None and str(guest_id).strip():
        return f"guest:{guest_id.strip()}"
    if scope_state is not None:
        state_user = scope_state.get("user")
        if state_user is not None:
            uid = getattr(state_user, "id", None) or (
                state_user.get("id") if isinstance(state_user, dict) else None
            )
            if isinstance(uid, str) and uid.strip():
                return f"user:{uid.strip()}"
        state_guest = scope_state.get("guest_id")
        if isinstance(state_guest, str) and state_guest.strip():
            return f"guest:{state_guest.strip()}"
    if cookies is not None:
        guest_cookie = read_cookie_field(cookies, DEFAULT_GUEST_COOKIE_NAME)
        if isinstance(guest_cookie, str) and guest_cookie.strip():
            return f"guest:{guest_cookie.strip()}"
    return _ANON_SUBJECT


def resolve_gql_subject_from_context(context: object | None) -> str:
    if not isinstance(context, dict):
        return _ANON_SUBJECT
    explicit = context.get("subject")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    request = context.get("request")
    if request is None:
        return _ANON_SUBJECT
    scope = getattr(request, "scope", None)
    scope_state = scope.get("state") if isinstance(scope, dict) else None
    state = getattr(request, "state", None)
    user = getattr(state, "user", None) if state is not None else None
    user_id = getattr(user, "id", None) if user is not None else None
    guest_id = None
    if isinstance(scope_state, dict):
        guest_id = scope_state.get("guest_id")
    cookies = getattr(request, "cookies", None)
    return resolve_gql_subject(
        user_id=str(user_id) if user_id is not None else None,
        guest_id=str(guest_id) if guest_id is not None else None,
        cookies=cookies,
        scope_state=scope_state if isinstance(scope_state, dict) else None,
    )


__all__ = ["resolve_gql_subject", "resolve_gql_subject_from_context"]
