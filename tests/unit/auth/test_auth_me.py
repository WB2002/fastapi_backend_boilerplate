from __future__ import annotations

from app.auth.auth_controller import AuthController
from app.auth.types import AuthenticatedUser


def test_get_me_returns_id_and_email_only() -> None:
    controller = AuthController(auth_service=object())  # type: ignore[arg-type]
    user = AuthenticatedUser(
        id="user-123",
        email="user@example.com",
        role="user",
        jti="session-jti",
    )

    profile = controller.get_me(user)

    assert profile == {"id": "user-123", "email": "user@example.com"}
