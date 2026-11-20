from __future__ import annotations

from app.main import create_app


def test_example_jobs_router_absent_in_production(
    valid_base_env: dict[str, str],
) -> None:
    env = {
        **valid_base_env,
        "NODE_ENV": "production",
        "ALLOWED_ORIGINS": "https://app.example.com",
    }
    app = create_app(env=env, validate_environment=False)
    paths = list(app.openapi()["paths"])
    assert not any(path.startswith("/example/jobs") for path in paths)


def test_example_jobs_router_present_in_development(
    valid_base_env: dict[str, str],
) -> None:
    env = {**valid_base_env, "NODE_ENV": "development"}
    app = create_app(env=env, validate_environment=False)
    paths = list(app.openapi()["paths"])
    assert any(path.startswith("/example/jobs") for path in paths)
