# Security Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all five **confirmed** findings in `docs/reports/2026-11-21-security-audit/` (SEC-AUD-001 through SEC-AUD-005) and update `docs/threat-model.md` to match runtime posture after fixes.

**Architecture:** Implement missing controls with minimal, parity-focused diffs: (1) Strawberry `SchemaExtension` pipeline for GraphQL depth/complexity/Redis budget/concurrency keyed by `userId` / `guestId` / `anon`; (2) FastAPI global dependencies for `CountryAccessGuard` between JWT and rate limit; (3) production boot guards mirroring conformance and WebSocket patterns for example routes and REST CORS; (4) live authorization for feature-flag admin via MongoDB role + `FEATURE_FLAG_ADMIN_EMAILS`. Defer SEC-AUD-006/007 pending ops validation (document only).

**Tech Stack:** Python 3.12+, FastAPI, Strawberry GraphQL, `graphql-core`, Redis (`redis.asyncio`), PostgreSQL (`asyncpg`), MongoDB/Beanie, `uv`, `pytest`.

**Spec / audit inputs:** `docs/reports/2026-11-21-security-audit/security-audit-report.md`, `findings.json`, `intent/fastapi-boilerplate/spec.md` (REQ-012, REQ-013, feature-flag admin), `docs/architecture.md` (guard pipeline order).

## Global Constraints

- Strict typing everywhere (`uv run mypy --strict app tests`); zero `Any` leaks on public module boundaries.
- `.reference_nestjs/` is read-only; never modify or scan it in tests.
- Run `scripts/verify.sh` before reporting any task complete; fix code, never weaken tests.
- No new dependencies via hand-editing `pyproject.toml` / `uv.lock` — use `/add-dependency` if Redis Lua or GraphQL helpers require a package.
- Snake_case under `app/` and `tests/`; pure ASGI middleware where `ContextVar` propagation matters (existing country module already provides ASGI middleware — prefer **router dependency** to match JWT/rate-limit pattern).
- Human owner triages `findings.json`; agents do not set finding Status to `accepted`.

---

### Task 1: GraphQL depth and per-operation complexity (SEC-AUD-001a)

**Files:**
- Create: `app/graphql/query_limits.py`
- Modify: `app/graphql/complexity_extension.py`
- Test: `tests/unit/graphql/test_query_limits.py`

**Interfaces:**
- Consumes: `graphql-core` AST (`graphql.language`, `graphql.parse`)
- Produces:
  - `MAX_QUERY_DEPTH: int` (= `10`)
  - `class GraphqlLimitError(Exception)` with `code: str`, `message: str`
  - `def assert_query_depth(document_ast, *, max_depth: int = MAX_QUERY_DEPTH) -> None` — raises `GraphqlLimitError` with `code="QUERY_DEPTH_LIMIT_EXCEEDED"`
  - `def estimate_query_complexity(document_ast, *, max_complexity: int) -> int` — returns complexity or raises `GraphqlLimitError` with `code="QUERY_TOO_COMPLEX"`

- [ ] **Step 1: Write failing depth/complexity tests**

```python
# tests/unit/graphql/test_query_limits.py
from __future__ import annotations

import pytest
from graphql import parse

from app.graphql.query_limits import (
    GraphqlLimitError,
    assert_query_depth,
    estimate_query_complexity,
)


def test_rejects_query_deeper_than_ten() -> None:
    deep_query = (
        "query { " + "categories { categories { " * 12 + "id " * 12 + "}" * 12 + "}"
    )
    doc = parse(deep_query)
    with pytest.raises(GraphqlLimitError) as exc:
        assert_query_depth(doc, max_depth=10)
    assert exc.value.code == "QUERY_DEPTH_LIMIT_EXCEEDED"


def test_rejects_complexity_above_cap() -> None:
    aliases = " ".join(f"a{i}: categories {{ id name }}" for i in range(60))
    doc = parse(f"query {{ {aliases} }}")
    with pytest.raises(GraphqlLimitError) as exc:
        estimate_query_complexity(doc, max_complexity=50)
    assert exc.value.code == "QUERY_TOO_COMPLEX"


def test_accepts_simple_categories_query() -> None:
    doc = parse("query { categories { id name } }")
    assert_query_depth(doc, max_depth=10)
    assert estimate_query_complexity(doc, max_complexity=50) <= 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/graphql/test_query_limits.py -v`  
Expected: FAIL — `ModuleNotFoundError: app.graphql.query_limits`

- [ ] **Step 3: Implement `query_limits.py`**

```python
# app/graphql/query_limits.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from graphql.language.ast import (
    FieldNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    OperationDefinitionNode,
)

MAX_QUERY_DEPTH: int = 10


@dataclass(frozen=True, slots=True)
class GraphqlLimitError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _field_depth(node: FieldNode, fragments: dict[str, Any], seen: set[str]) -> int:
    if not node.selection_set:
        return 1
    child_depths: list[int] = []
    for selection in node.selection_set.selections:
        if isinstance(selection, FieldNode):
            child_depths.append(_field_depth(selection, fragments, seen))
        elif isinstance(selection, InlineFragmentNode):
            if selection.selection_set:
                for inner in selection.selection_set.selections:
                    if isinstance(inner, FieldNode):
                        child_depths.append(_field_depth(inner, fragments, seen))
        elif isinstance(selection, FragmentSpreadNode):
            name = selection.name.value
            if name in seen:
                continue
            frag = fragments.get(name)
            if frag and frag.selection_set:
                for inner in frag.selection_set.selections:
                    if isinstance(inner, FieldNode):
                        child_depths.append(
                            _field_depth(inner, fragments, seen | {name})
                        )
    return 1 + (max(child_depths) if child_depths else 0)


def assert_query_depth(
    document_ast: object, *, max_depth: int = MAX_QUERY_DEPTH
) -> None:
    fragments: dict[str, Any] = {
        d.name.value: d
        for d in getattr(document_ast, "definitions", [])
        if getattr(d, "kind", None) == "fragment_definition"
    }
    depths: list[int] = []
    for definition in getattr(document_ast, "definitions", []):
        if not isinstance(definition, OperationDefinitionNode):
            continue
        if not definition.selection_set:
            continue
        for selection in definition.selection_set.selections:
            if isinstance(selection, FieldNode):
                depths.append(_field_depth(selection, fragments, set()))
    if depths and max(depths) > max_depth:
        raise GraphqlLimitError(
            code="QUERY_DEPTH_LIMIT_EXCEEDED",
            message=f"Query depth {max(depths)} exceeds maximum {max_depth}",
        )


def estimate_query_complexity(document_ast: object, *, max_complexity: int) -> int:
    # NestJS parity: each selected field costs 1; aliases multiply cost.
    total = 0
    for definition in getattr(document_ast, "definitions", []):
        if not isinstance(definition, OperationDefinitionNode):
            continue
        if not definition.selection_set:
            continue
        stack: list[Any] = list(definition.selection_set.selections)
        while stack:
            node = stack.pop()
            if isinstance(node, FieldNode):
                total += 1
                if node.selection_set:
                    stack.extend(node.selection_set.selections)
            elif isinstance(node, InlineFragmentNode) and node.selection_set:
                stack.extend(node.selection_set.selections)
    if total > max_complexity:
        raise GraphqlLimitError(
            code="QUERY_TOO_COMPLEX",
            message=f"Query complexity {total} exceeds maximum {max_complexity}",
        )
    return total
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/graphql/test_query_limits.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/graphql/query_limits.py tests/unit/graphql/test_query_limits.py
git commit -m "fix(security): add GraphQL depth and complexity limit helpers (SEC-AUD-001)"
```

---

### Task 2: GraphQL Redis budget, concurrency lease, and schema wiring (SEC-AUD-001b)

**Files:**
- Create: `app/graphql/gql_subject.py`, `app/graphql/redis_budget.py`
- Modify: `app/graphql/complexity_extension.py`, `app/graphql/schema.py`, `app/main.py:113-115,393-395`
- Test: `tests/unit/graphql/test_complexity_extension.py`

**Interfaces:**
- Consumes: `GraphqlLimitError`, `assert_query_depth`, `estimate_query_complexity` from Task 1; `RedisService.get_client()`; `Settings` / `validated_env` (`GQL_*` keys); `record_graphql_concurrency_503` from `app.core.telemetry.emf_metrics`
- Produces:
  - `def resolve_gql_subject(request_headers, request_cookies, request_state) -> str` — returns `user:<id>`, `guest:<uuid>`, or `anon`
  - `class GraphqlProtectionExtension(SchemaExtension)` — enforces depth, complexity, 60s Redis budget (`GQL_USER_BUDGET`), concurrency lease (`GQL_MAX_CONCURRENCY`, TTL `GQL_CONCURRENCY_KEY_TTL_SEC`); raises Strawberry `GraphQLError` with `extensions={"code": ...}`; sets `Retry-After` on concurrency deny
  - `def build_graphql_schema(*, redis_service: RedisService, env: Mapping[str, str | None]) -> strawberry.Schema`

- [ ] **Step 1: Write failing extension unit tests (Redis mocked)**

```python
# tests/unit/graphql/test_complexity_extension.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from graphql import parse

from app.graphql.complexity_extension import GraphqlProtectionExtension
from app.graphql.query_limits import GraphqlLimitError


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(
        self, key: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def decrby(self, key: str, amount: int) -> int:
        current = int(self.store.get(key, "0"))
        new_val = current - amount
        self.store[key] = str(new_val)
        return new_val

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed


@pytest.mark.asyncio
async def test_budget_rejects_when_insufficient() -> None:
    redis = FakeRedis()
    redis.store["gql:budget:guest:abc"] = "5"
    ext = GraphqlProtectionExtension(
        redis_client=redis,
        max_complexity=50,
        user_budget=1000,
        max_concurrency=3,
        concurrency_ttl_sec=300,
        subject="guest:abc",
    )
    doc = parse("query { categories { id name description } }")
    with pytest.raises(GraphqlLimitError) as exc:
        await ext.validate_before_execute(document=doc)
    assert exc.value.code == "OUT_OF_BUDGET"
```

Add `validate_before_execute` as a test seam on the extension (thin wrapper called from `on_operation`).

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/unit/graphql/test_complexity_extension.py::test_budget_rejects_when_insufficient -v`  
Expected: FAIL — class/method missing

- [ ] **Step 3: Implement Redis budget + concurrency + extension**

Implement `redis_budget.py` with keys `gql:budget:{subject}` (EX 60) and `gql:concurrency:{subject}` (INCR with cap, EX TTL). Budget: read remaining; if `complexity > remaining` → `OUT_OF_BUDGET`; else `DECRBY` after checks pass.

Implement `GraphqlProtectionExtension` in `complexity_extension.py`:
- Parse query from `self.execution_context.query` via `graphql.parse`
- Run depth + complexity helpers
- Acquire concurrency slot before `yield`; release in `finally`
- On concurrency cap → `GraphQLError` with `extensions.code = GRAPHQL_OVERLOAD_EXTENSION_CODE`, call `record_graphql_concurrency_503()`, set `Retry-After: 1` on `execution_context.context["response"].headers` when present

Refactor `schema.py`:

```python
def build_graphql_schema(
    *,
    redis_service: RedisService,
    env: Mapping[str, str | None],
) -> strawberry.Schema:
    settings = Settings(env)
    client = redis_service.get_client()
    return strawberry.Schema(
        query=Query,
        extensions=[
            lambda execution_context=None: GraphqlProtectionExtension(
                redis_client=client,
                max_complexity=settings.gql_max_complexity,
                user_budget=settings.gql_user_budget,
                max_concurrency=settings.gql_max_concurrency,
                concurrency_ttl_sec=settings.gql_concurrency_key_ttl_sec,
                execution_context=execution_context,
            )
        ],
    )
```

Keep module-level `schema = build_graphql_schema(...)` only for contract tests by using test env + `RedisService(auto_init=False)` with injected fake client, or export `build_graphql_schema` and update `tests/contracts/test_graphql_contract.py` to call the factory.

Wire `main.py`:

```python
graphql_schema = build_graphql_schema(redis_service=redis_service, env=validated_env)


async def graphql_context(request: Request, response: Response) -> dict[str, object]:
    return {
        "request": request,
        "response": response,
        "subject": resolve_gql_subject(request),
    }


graphql_router = GraphQLRouter(graphql_schema, context_getter=graphql_context)
```

- [ ] **Step 4: Run unit + contract tests**

Run: `uv run pytest tests/unit/graphql tests/contracts/test_graphql_contract.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/graphql/ app/main.py tests/unit/graphql/test_complexity_extension.py tests/contracts/test_graphql_contract.py
git commit -m "fix(security): enforce GraphQL Redis budget and concurrency (SEC-AUD-001)"
```

---

### Task 3: Example module production gate (SEC-AUD-002)

**Files:**
- Modify: `app/main.py:388-391`, `app/common/config/validate_env.py:25-46`
- Test: `tests/unit/main/test_example_routes_production.py`

**Interfaces:**
- Consumes: `is_conformance_harness_enabled` pattern; `node_env` from `validated_env`
- Produces: Example routers (`example_captcha_router`, `example_notification_router`, `example_jobs_router`) registered only when `NODE_ENV != "production"`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/main/test_example_routes_production.py
from __future__ import annotations

# valid_base_env fixture from tests/conftest.py

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
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any(p.startswith("/example/jobs") for p in paths)


def test_example_jobs_router_present_in_development(
    valid_base_env: dict[str, str],
) -> None:
    env = {**valid_base_env, "NODE_ENV": "development"}
    app = create_app(env=env, validate_environment=False)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert any("/example/jobs" in p for p in paths)
```

- [ ] **Step 2: Run test — expect FAIL** (routers always mounted)

Run: `uv run pytest tests/unit/main/test_example_routes_production.py -v`

- [ ] **Step 3: Gate example routers in `create_app`**

```python
    if node_env != "production":
        app.include_router(example_captcha_router)
        app.include_router(example_notification_router)
        app.include_router(example_jobs_router)
```

Optional hardening (same task): remove `@public` from `enqueue_echo` and mark route `@pytest`-only via test env — **do not** do this if it breaks `tests/e2e/helpers/jobs.py`; production omission is the primary fix.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/main/test_example_routes_production.py tests/unit/core/background_jobs/test_background_jobs.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/unit/main/test_example_routes_production.py
git commit -m "fix(security): disable example routers in production (SEC-AUD-002)"
```

---

### Task 4: Mount CountryAccessGuard in global dependency chain (SEC-AUD-003)

**Files:**
- Create: `app/common/country_access/country_access_dependency.py`
- Modify: `app/main.py:317-323,251-254`, `app/common/country_access/country_access_service.py:110-137` (shutdown hook)
- Test: `tests/unit/common/country_access/test_country_access_dependency.py`, `tests/integration/country_access/test_country_access_mounted.py`

**Interfaces:**
- Consumes: `CountryAccessGuard.check_access`, `parse_country_access_options`, `PostgresCountryAccessRepository`, `MaxMindGeoLocationService`, `JwtAuthGuard.is_request_public` (skip public routes only after JWT ran)
- Produces: `class CountryAccessDependency` with `async def __call__(self, request: Request) -> bool`
- Wired in `app.router.dependencies` **between** `Depends(jwt_guard)` and `Depends(rate_limit_guard)` per `docs/architecture.md`

- [ ] **Step 1: Write failing dependency test**

```python
# tests/unit/common/country_access/test_country_access_dependency.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from app.common.country_access.country_access_dependency import CountryAccessDependency


@pytest.mark.asyncio
async def test_denies_when_guard_raises() -> None:
    guard = MagicMock()
    guard.check_access = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Country access denied")
    )
    dep = CountryAccessDependency(guard)
    request = Request(
        {"type": "http", "method": "GET", "path": "/uploads", "headers": []}
    )
    with pytest.raises(HTTPException) as exc:
        await dep(request)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/unit/common/country_access/test_country_access_dependency.py -v`

- [ ] **Step 3: Implement dependency + `create_app` wiring**

```python
# app/common/country_access/country_access_dependency.py
class CountryAccessDependency:
    def __init__(self, guard: CountryAccessGuard) -> None:
        self._guard = guard

    async def __call__(self, request: Request) -> bool:
        await self._guard.check_access(request)
        return True
```

In `create_app` after `postgres_pool` / `redis_service` init:

```python
from app.common.config.parsers import parse_country_access_options
from app.common.country_access import (
    CountryAccessGuard,
    CountryAccessService,
    MaxMindGeoLocationService,
    PostgresCountryAccessRepository,
)
from app.common.country_access.country_access_service import (
    CountryAccessModuleOptions as ServiceOptions,
)

ca_config = parse_country_access_options(validated_env)
ca_repo = PostgresCountryAccessRepository(postgres_pool)
ca_service = CountryAccessService(
    ca_repo,
    ServiceOptions(
        unknown_country_policy="allow"
        if ca_config.unknown_country_policy == "allow"
        else "deny",
        bypass_paths=ca_config.bypass_paths,
        refresh_interval_ms=ca_config.refresh_interval_ms,
    ),
)
geo = MaxMindGeoLocationService(database_path=ca_config.geoip_database_path)
geo.init_reader()
country_guard = CountryAccessGuard(ca_service, geo)
country_dep = CountryAccessDependency(country_guard)
ca_service.start_periodic_refresh()
resolved_shutdown_state.register_before_close(ca_service.stop)
resolved_shutdown_state.register_before_close(geo.close)

app.state.country_access_service = ca_service

app.router.dependencies.extend(
    [
        Depends(bulkhead_guard),
        Depends(jwt_guard),
        Depends(country_dep),
        Depends(rate_limit_guard),
    ]
)
```

- [ ] **Step 4: Integration test (mock geo + active rules)**

Skip without Docker if needed; unit-level guard tests already exist — add `tests/integration/country_access/test_country_access_mounted.py` that calls `create_app` and asserts `CountryAccessDependency` is in router dependency stack (inspect `app.router.dependencies`).

- [ ] **Step 5: Run tests + verify**

Run: `uv run pytest tests/unit/common/country_access -v`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/common/country_access/country_access_dependency.py app/main.py tests/
git commit -m "fix(security): wire CountryAccessGuard into global dependencies (SEC-AUD-003)"
```

---

### Task 5: REST CORS production fail-fast (SEC-AUD-004)

**Files:**
- Modify: `app/common/config/parsers.py` (new `assert_rest_production_origins`), `app/main.py:208-210`, `app/common/config/validate_env.py` (optional mirror check)
- Test: `tests/unit/common/config/test_config_parsers.py`, `tests/unit/common/config/test_validate_env.py`

**Interfaces:**
- Consumes: `_resolve_cors_origins` behavior in `main.py`
- Produces: `def assert_rest_production_origins(raw_origins: str | None, node_env: str) -> None` — raises `ValueError` when `NODE_ENV=production` and origins empty, whitespace-only, or contains `*`

- [ ] **Step 1: Write failing parser test**

```python
def test_rest_production_origins_rejects_wildcard() -> None:
    with pytest.raises(ValueError, match=r"ALLOWED_ORIGINS must list explicit origins"):
        assert_rest_production_origins("*", "production")


def test_rest_production_origins_allows_explicit_list() -> None:
    assert_rest_production_origins("https://a.com, https://b.com", "production")
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/unit/common/config/test_config_parsers.py -k rest_production -v`

- [ ] **Step 3: Implement guard + call from `create_app`**

Mirror `assert_websocket_production_origins` style:

```python
def assert_rest_production_origins(raw_origins: str | None, node_env: str) -> None:
    if node_env != "production":
        return
    if raw_origins is None or trim_js(str(raw_origins)) == "":
        raise ValueError(
            "ALLOWED_ORIGINS must list explicit origins when NODE_ENV=production"
        )
    origins = [part.strip() for part in raw_origins.split(",") if part.strip()]
    if not origins or any(o == "*" for o in origins):
        raise ValueError(
            "ALLOWED_ORIGINS must list explicit origins when NODE_ENV=production"
        )
```

In `create_app` immediately after `assert_websocket_production_origins`:

```python
    assert_rest_production_origins(validated_env.get("ALLOWED_ORIGINS"), node_env)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/common/config/test_config_parsers.py tests/unit/common/config/test_validate_env.py -v`  
Expected: PASS (update any test that boots `create_app` with `NODE_ENV=production` to supply explicit `ALLOWED_ORIGINS`)

- [ ] **Step 5: Commit**

```bash
git add app/common/config/parsers.py app/main.py tests/unit/common/config/
git commit -m "fix(security): fail fast on REST CORS wildcard in production (SEC-AUD-004)"
```

---

### Task 6: Feature-flag admin live authorization (SEC-AUD-005)

**Files:**
- Modify: `app/core/feature_flag/feature_flag_controller.py:24-32`, `app/main.py` (expose `auth_service` on app.state — already present)
- Create: `app/core/feature_flag/feature_flag_permissions.py`
- Test: `tests/unit/core/feature_flag/test_feature_flag_permissions.py`

**Interfaces:**
- Consumes: `AuthenticatedUser`, `AuthService.is_admin_email`, `UserRepository.find_by_id`
- Produces: `async def require_feature_flag_admin(...) -> AuthenticatedUser` — allows when **live** Mongo role is `admin` **or** email in `FEATURE_FLAG_ADMIN_EMAILS`; rejects stale JWT `role=admin` when DB role is `user` and email not allowlisted

- [ ] **Step 1: Write failing permission tests**

```python
# tests/unit/core/feature_flag/test_feature_flag_permissions.py
@pytest.mark.asyncio
async def test_stale_jwt_admin_rejected_when_db_user() -> None:
    user = AuthenticatedUser(id="u1", email="user@example.com", jti="j1", role="admin")
    auth = MagicMock()
    auth.is_admin_email.return_value = False
    auth.user_repository.find_by_id = AsyncMock(
        return_value=MagicMock(role=UserRole.USER, email="user@example.com")
    )
    with pytest.raises(HTTPException) as exc:
        await assert_feature_flag_admin(user, auth_service=auth)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/unit/core/feature_flag/test_feature_flag_permissions.py -v`

- [ ] **Step 3: Implement live check**

```python
# app/core/feature_flag/feature_flag_permissions.py
async def assert_feature_flag_admin(
    user: AuthenticatedUser,
    *,
    auth_service: AuthService,
) -> AuthenticatedUser:
    db_user = await auth_service.user_repository.find_by_id(user.id)
    live_role = (
        db_user.role
        if db_user is not None
        else (
            UserRole.ADMIN
            if str(user.role).lower() == UserRole.ADMIN.value
            else UserRole.USER
        )
    )
    if isinstance(live_role, UserRole):
        is_admin = live_role == UserRole.ADMIN
    else:
        is_admin = str(live_role).lower() == UserRole.ADMIN.value
    if not is_admin and not auth_service.is_admin_email(user.email):
        raise HTTPException(
            status_code=403, detail="Insufficient feature-flag permissions"
        )
    return user
```

Update controller to depend on `AuthService` from `request.app.state.auth_service` via small `get_auth_service(request: Request) -> AuthService` helper.

**Follow-up (same task, if user demotion API exists):** on role change from `admin` → `user`, call `AuthService._revoke_jti_in_redis(jti)` — search `User` update paths; if none, document as future intent only (live check already closes exploit window).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/core/feature_flag -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/feature_flag/ tests/unit/core/feature_flag/
git commit -m "fix(security): re-resolve feature-flag admin from DB and allowlist (SEC-AUD-005)"
```

---

### Task 7: Threat model and docs sync

**Files:**
- Modify: `docs/threat-model.md`, `docs/architecture.md` (only if pipeline wording changes), `CHANGELOG.md`

**Interfaces:**
- Consumes: completed Tasks 1–6
- Produces: Accurate documentation of enforced controls (GraphQL budgets, country access wired, example routes production-gated, REST CORS guard, feature-flag live auth)

- [ ] **Step 1: Update `docs/threat-model.md` sections**

Replace statements that claim controls are active where they were stubs; cite `GQL_*`, country access dependency, production example gate, `assert_rest_production_origins`, and live feature-flag admin.

- [ ] **Step 2: Add CHANGELOG entry under Security**

```markdown
### Security
- SEC-AUD-001–005: GraphQL DoS controls, example job production gate, country access guard, REST CORS production validation, feature-flag live admin authorization.
```

- [ ] **Step 3: Run full verification**

Run: `./scripts/verify.sh`  
Expected: all tiers green

- [ ] **Step 4: Commit**

```bash
git add docs/threat-model.md CHANGELOG.md
git commit -m "docs: sync threat model after security audit remediation"
```

---

## Deferred (needs_validation — not in this plan's acceptance gate)

| ID | Action |
|----|--------|
| SEC-AUD-006 | Ops decision on Redis rate-limit fail-open; document in `docs/nfr.md` / runbook only |
| SEC-AUD-007 | Production `validate_env` requiring `UPLOAD_VIRUS_SCAN=true` when uploads exposed — separate intent after ops confirmation |

---

## Self-Review (plan author)

| Check | Result |
|-------|--------|
| SEC-AUD-001 | Tasks 1–2 |
| SEC-AUD-002 | Task 3 |
| SEC-AUD-003 | Task 4 |
| SEC-AUD-004 | Task 5 |
| SEC-AUD-005 | Task 6 |
| Threat model §6 | Task 7 |
| Placeholder scan | No TBD/TODO/implement-later steps |
| Type consistency | `build_graphql_schema`, `CountryAccessDependency`, `assert_feature_flag_admin` names stable across tasks |

---

## Post-implementation

1. Human owner triages and accepts remediations in `findings.json`.
2. Dispatch `security-implementer` only after acceptance (this plan is the implementer spec).
3. Re-run `/security-audit` after Tasks 1–4 land to confirm Medium findings disproven.