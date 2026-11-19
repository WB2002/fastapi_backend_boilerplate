import tomllib
from pathlib import Path

import yaml

import app
import app.common
import app.common.config
import app.common.constants
import app.common.decorators
import app.common.utils
import app.types
from app.common.constants import (
    ACCESS_TOKEN_COOKIE,
    CSRF_TOKEN_COOKIE,
    DEFAULT_GUEST_COOKIE_MAX_AGE,
    DEFAULT_GUEST_COOKIE_NAME,
    IS_PUBLIC_KEY,
    LOG_COLOR_MAPPER,
    SESSION_ID_COOKIE,
)
from app.common.decorators import is_public_endpoint, public
from app.common.utils import without_none
from app.types import AuthenticatedUser, ILoggerContext, JwtPayload

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_core_packages_importable() -> None:
    assert app is not None
    assert app.types is not None
    assert app.common is not None
    assert app.common.constants is not None
    assert app.common.decorators is not None
    assert app.common.utils is not None
    assert app.common.config is not None


def test_types_and_constants_shapes() -> None:
    user: AuthenticatedUser = {"id": "user-1", "email": "a@example.com", "role": "user"}
    jwt_payload: JwtPayload = {
        "sub": "user-1",
        "email": "a@example.com",
        "jti": "jti-1",
        "role": "user",
    }
    log_ctx: ILoggerContext = {"requestId": "req-1", "userId": "user-1"}

    assert user["id"] == "user-1"
    assert jwt_payload["sub"] == "user-1"
    assert log_ctx["requestId"] == "req-1"
    assert ACCESS_TOKEN_COOKIE == "access_token"
    assert SESSION_ID_COOKIE == "session_id"
    assert CSRF_TOKEN_COOKIE == "csrf_token"
    assert DEFAULT_GUEST_COOKIE_NAME == "guest_id"
    assert DEFAULT_GUEST_COOKIE_MAX_AGE == 31_536_000
    assert IS_PUBLIC_KEY == "isPublic"
    assert "reset" in LOG_COLOR_MAPPER


def test_public_decorator() -> None:
    @public
    def public_handler() -> str:
        return "ok"

    def private_handler() -> str:
        return "secret"

    assert public_handler() == "ok"
    assert is_public_endpoint(public_handler) is True
    assert is_public_endpoint(private_handler) is False


def test_without_none_preserves_falsy_non_none_values() -> None:
    payload: dict[str, int | bool | str | None] = {
        "zero": 0,
        "flag": False,
        "empty": "",
        "missing": None,
    }
    assert without_none(payload) == {"zero": 0, "flag": False, "empty": ""}


def test_pyproject_exclusions_and_python_version() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.12"
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["app"]
    assert ".reference_nestjs" in data["tool"]["ruff"]["extend-exclude"]
    assert any(".reference_nestjs" in pat for pat in data["tool"]["mypy"]["exclude"])
    assert ".reference_nestjs" in data["tool"]["pytest"]["ini_options"]["norecursedirs"]


def test_docker_compose_parity() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    assert services["mongo"]["image"] == "mongo:8.0"
    assert services["redis"]["image"] == "redis:7"
    assert services["clamav"]["image"] == "clamav/clamav:1.4"
    assert "ports" not in services["clamav"]
    assert "healthcheck" in services["clamav"]
    assert "backend-network" in compose["networks"]


def test_gitignore_entries() -> None:
    content = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for expected in (
        ".env",
        ".venv/",
        "*.mmdb",
        ".reference_nestjs/",
        ".worktrees/",
        ".cursor/.guardrails-edit",
    ):
        assert expected in content
