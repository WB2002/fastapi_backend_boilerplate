from typing import Final

ACCESS_TOKEN_COOKIE: Final[str] = "access_token"  # noqa: S105
SESSION_ID_COOKIE: Final[str] = "session_id"
CSRF_TOKEN_COOKIE: Final[str] = "csrf_token"  # noqa: S105
DEFAULT_GUEST_COOKIE_NAME: Final[str] = "guest_id"
DEFAULT_GUEST_COOKIE_MAX_AGE: Final[int] = 31_536_000
