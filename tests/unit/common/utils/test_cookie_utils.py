import base64
import hashlib
import hmac

import pytest

from app.common.utils.cookie_utils import (
    get_access_token_cookie_options,
    get_csrf_token_cookie_options,
    get_guest_id_cookie_options,
    get_session_id_cookie_options,
    sign_cookie,
    unsign_cookie,
)


def test_access_token_cookie_options_dev_and_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NODE_ENV", "development")
    dev_opts = get_access_token_cookie_options(900)
    assert dev_opts == {
        "httponly": True,
        "secure": False,
        "samesite": "strict",
        "path": "/",
        "max_age": 900,
    }

    monkeypatch.setenv("NODE_ENV", "production")
    prod_opts = get_access_token_cookie_options(900)
    assert prod_opts["secure"] is True
    assert (
        get_access_token_cookie_options(900, node_env="development")["secure"] is False
    )


def test_session_id_cookie_options_dev_and_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NODE_ENV", "development")
    dev_opts = get_session_id_cookie_options(604_800)
    assert dev_opts == {
        "httponly": True,
        "secure": False,
        "samesite": "strict",
        "path": "/auth/refresh",
        "max_age": 604_800,
    }

    assert (
        get_session_id_cookie_options(604_800, node_env="production")["secure"] is True
    )


def test_csrf_token_cookie_options_readable_by_js(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NODE_ENV", "development")
    opts = get_csrf_token_cookie_options()
    assert opts["httponly"] is False
    assert opts["samesite"] == "strict"
    assert opts["path"] == "/"
    assert "max_age" not in opts
    assert opts["secure"] is False
    assert get_csrf_token_cookie_options(node_env="production")["secure"] is True


def test_guest_id_cookie_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_ENV", "development")
    dev_opts = get_guest_id_cookie_options()
    assert dev_opts == {
        "httponly": True,
        "secure": False,
        "samesite": "lax",
        "path": "/",
        "max_age": 31_536_000,
    }
    assert get_guest_id_cookie_options(node_env="production")["secure"] is True


def test_sign_cookie_format_matches_cookie_signature() -> None:
    secret = "super-secret"
    value = "550e8400-e29b-41d4-a716-446655440000"
    signed = sign_cookie(value, secret)

    digest = hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest()
    expected_sig = base64.b64encode(digest).decode("ascii").rstrip("=")
    assert signed == f"{value}.{expected_sig}"
    assert not signed.endswith("=")
    assert unsign_cookie(signed, secret) == value


def test_sign_cookie_empty_secret_raises() -> None:
    with pytest.raises(ValueError, match="Cookie secret must be non-empty"):
        sign_cookie("abc", "")


def test_unsign_cookie_valid_and_value_with_dots() -> None:
    secret = "my-secret"
    signed = sign_cookie("a.b.c", secret)
    assert unsign_cookie(signed, secret) == "a.b.c"


def test_unsign_cookie_rejects_tampered_wrong_secret_missing_dot() -> None:
    secret = "my-secret"
    signed = sign_cookie("guest-123", secret)
    val, _sep, sig = signed.rpartition(".")

    assert unsign_cookie(f"{val}-tampered.{sig}", secret) is None
    assert unsign_cookie(f"{val}.{sig[:-1]}X", secret) is None
    assert unsign_cookie(signed, "wrong-secret") is None
    assert unsign_cookie(signed, "") is None
    assert unsign_cookie("", secret) is None
    assert unsign_cookie("no-dot-here", secret) is None
