from __future__ import annotations

from app.common.country_access.country_access_dependency import CountryAccessDependency
from app.main import create_app


def test_create_app_registers_country_access_dependency(
    valid_base_env: dict[str, str],
) -> None:
    app = create_app(
        env={**valid_base_env, "NODE_ENV": "development"}, validate_environment=False
    )
    resolved = [getattr(dep, "dependency", None) for dep in app.router.dependencies]
    assert any(isinstance(item, CountryAccessDependency) for item in resolved)
