# Optional Hardening, Integration Testing & Verification Pipeline Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the post-baseline integration test suite (Testcontainers), unified full verification script (`scripts/verify-full.sh`), automated security scanning scripts (`scripts/ci/*`), and quality metric ratchet system (`quality/baseline.json` and `scripts/ratchet.sh`).

**Architecture:** Build Docker-isolated integration tests using `testcontainers-python` to validate live backing services (PostgreSQL, MongoDB, Redis, ClamAV) with fallback detection when Docker is absent. Stand up standalone CI security scanners (Semgrep, pip-audit, base64 secret checks, Mutmut mutation tests) strictly ignoring `.reference_nestjs/`. Implement automated measurement and update capabilities in `scripts/ratchet.sh` to enforce zero lint warnings, zero type escape hatches, and test coverage floors.

**Tech Stack:** Python 3.12+, `uv`, `pytest`, `testcontainers`, `asyncpg`, `motor`/`beanie`, `redis-py`, `ruff`, `mypy`, `pip-audit`, `semgrep`, `jq`, `bash`.

**Spec:** `intent/fastapi-boilerplate/plan.md § TASK-018` and `docs/adr/0001-stack.md`.

## Global Constraints

- Strict typing everywhere (`mypy --strict app tests`); zero `Any` leaks on module boundaries.
- `.reference_nestjs/` is strictly read-only and excluded from all scanners, linters, tests, and ratchet checks.
- Commands run via `uv run` with `uv.lock` frozen where applicable.
- All bash scripts must use `set -euo pipefail` and include `export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"`.
- Never weaken or delete existing passing unit (340), property (28), or contract (2) tests.

## Review Focus

- Docker daemon missing in restricted environments (tests must detect Docker socket presence and skip cleanly without failing `scripts/verify.sh`).
- Container startup latency causing test timeouts (use health checks and exponential readiness polling).
- Semgrep or scanner failure when external rule downloads are blocked in sandboxed runners (support offline fallback or gracefully warn).
- Inaccurate coverage reporting on lazy-loaded modules (ensure test runners import `app.main` before assessing statement coverage).
- Floating point precision differences in `jq` when comparing coverage floats in `scripts/ratchet.sh`.

---

### Task 1: Testcontainers Integration Test Suite (`tests/integration/`)

**Files:**
- Create: `tests/integration/test_polyglot_containers.py`
- Modify: `tests/integration/__init__.py`
- Test: `tests/integration/test_polyglot_containers.py`

**Interfaces:**
- Consumes: `tests/e2e/helpers/containers.py` (`E2EBackingServices`, `PostgresConfig`, `mongo_direct_connection_uri`), `app.common.postgres.postgres_pool.PostgresPool`, `app.common.redis.redis_service.RedisService`, `app.common.mongo.mongo_service.MongoService`
- Produces: Live backing service integration tests verifying schema migrations (001, 002), Redis atomic distributed locks, MongoDB Beanie persistence, and ClamAV socket ping.

- [x] **Step 1: Write the integration test suite with Docker detection guard**

```python
# tests/integration/test_polyglot_containers.py
from __future__ import annotations

import os
import shutil
import pytest
from app.common.postgres.postgres_pool import PostgresPool
from app.common.redis.redis_service import RedisService
from app.common.mongo.mongo_service import MongoService


def is_docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return os.path.exists("/var/run/docker.sock") or bool(os.environ.get("DOCKER_HOST"))


pytestmark = pytest.mark.skipif(
    not is_docker_available(), reason="Docker daemon unavailable for testcontainers"
)


@pytest.mark.asyncio
async def test_postgres_migrations_and_query() -> None:
    # Spins up postgres via testcontainers, runs migrations 001 and 002, queries background_jobs
    ...
```

- [x] **Step 2: Run test to verify it executes or skips cleanly when Docker is absent**

Run: `uv run pytest tests/integration -v`
Expected: PASS (or SKIPPED with "Docker daemon unavailable for testcontainers")

- [x] **Step 3: Implement complete live container fixtures for PostgreSQL, Redis, and MongoDB in `tests/integration/test_polyglot_containers.py`**

Connect to PostgreSQL, execute `PostgresMigrator.up()`, test `PostgresJobRepository.insert_pending` and `claim_by_id`. Connect to Redis, test `acquire_redis_loader_lock` and `release_redis_loader_lock` Lua script. Connect to MongoDB, test `User.insert()` and `Session.insert()`.

- [x] **Step 4: Run integration test suite**

Run: `uv run pytest tests/integration -v`
Expected: PASS (all tests pass or skip appropriately)

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "feat(tests): add testcontainers integration test suite with docker fallback"
```

---

### Task 2: Full Verification Script (`scripts/verify-full.sh`)

**Files:**
- Modify: `scripts/verify-full.sh:1-10`

**Interfaces:**
- Consumes: `scripts/verify.sh`, `scripts/check-docs.sh`, `scripts/check-skills.sh`, `tests/integration`
- Produces: Top-level release gate command `./scripts/verify-full.sh` exiting 0 only when all static, unit, property, contract, doc, and integration checks pass.

- [x] **Step 1: Replace placeholder stub in `scripts/verify-full.sh`**

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "== 1. standard verification =="
./scripts/verify.sh

echo "== 2. documentation & skill checks =="
./scripts/check-docs.sh
./scripts/check-skills.sh

echo "== 3. integration test suite =="
if command -v docker >/dev/null 2>&1 && (docker info >/dev/null 2>&1 || [ -S /var/run/docker.sock ]); then
  uv run pytest tests/integration
else
  echo "notice: Docker unavailable; skipping live container integration suite"
fi

echo "verify-full: OK"
```

- [x] **Step 2: Run `scripts/verify-full.sh` to test execution**

Run: `./scripts/verify-full.sh`
Expected: Output includes "== 1. standard verification ==", "verify: OK", "== 2. documentation & skill checks ==", "verify-full: OK", exiting 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify-full.sh
git commit -m "feat(ci): configure verify-full.sh release gate"
```

---

### Task 3: Security & CI Scanning Scripts (`scripts/ci/`)

**Files:**
- Create: `scripts/ci/scan-long-base64-literals.sh`
- Create: `scripts/ci/run-pip-audit.sh`
- Create: `scripts/ci/run-semgrep.sh`
- Create: `scripts/ci/run-mutation.sh`

**Interfaces:**
- Consumes: `app/`, `tests/`, `pyproject.toml`, `uv.lock`. Excludes `.reference_nestjs/`.
- Produces: Standalone bash scripts for static security analysis, supply-chain vulnerability checks, and mutation score evaluation.

- [x] **Step 1: Implement `scripts/ci/scan-long-base64-literals.sh`**

Create script that inspects source code in `app/` using Python to detect any raw base64 string literals >= 64 characters that are not in an allowed allowlist (e.g. standard CSP policy hashes or test keys). Ensure it ignores `.reference_nestjs/`.

- [x] **Step 2: Implement `scripts/ci/run-pip-audit.sh`**

Create script that runs `uv run pip-audit` or parses dependencies against OSV/PyPI advisory databases, exiting non-zero if known vulnerabilities exist.

- [x] **Step 3: Implement `scripts/ci/run-semgrep.sh`**

Create script that runs `uv run semgrep scan --config auto --error app/` (excluding `.reference_nestjs/` and `tests/`), detecting security anti-patterns (SSRF, SQL injection, hardcoded credentials).

- [x] **Step 4: Implement `scripts/ci/run-mutation.sh`**

Create script that identifies git-modified files in `app/` and runs `uv run mutmut run --paths-to-mutate <files>`, verifying that mutations are caught by tests.

- [x] **Step 5: Make all scripts executable and verify**

Run: `chmod +x scripts/ci/*.sh && ./scripts/ci/scan-long-base64-literals.sh`
Expected: Exits 0 with "base64 scan: OK".

- [ ] **Step 6: Commit**

```bash
git add scripts/ci/
git commit -m "feat(security): add CI security scanning scripts"
```

---

### Task 4: Quality Ratchet Metrics Baseline Configuration (`quality/baseline.json` & `scripts/ratchet.sh`)

**Files:**
- Modify: `quality/baseline.json`
- Modify: `scripts/ratchet.sh`

**Interfaces:**
- Consumes: `ruff check`, `mypy`, `pytest --cov=app --cov-report=json`, `pyproject.toml`
- Produces: `./scripts/ratchet.sh` and `./scripts/ratchet.sh --update` enforcing that code quality metrics never degrade below committed baselines.

- [x] **Step 1: Write automated metric collection in `scripts/ratchet.sh`**

Measure:
- `test_coverage`: calculate total percentage from `coverage.json`.
- `lint_warnings`: `uv run ruff check . --output-format json | jq '. | length'`.
- `type_escape_hatches`: count of `# type: ignore` or `cast(` in `app/`.
- `dead_code`: 0 (enforced via ruff unused import/variable rules).
- `dependency_count`: count of direct dependencies in `pyproject.toml`.
- `max_complexity`: cyclomatic complexity ceiling measured via `uv run ruff check --select C901`.

- [x] **Step 2: Add `--update` flag support to `scripts/ratchet.sh`**

When called with `--update`, write measured metrics into `quality/baseline.json`.

- [x] **Step 3: Populate baseline metrics**

Run: `./scripts/ratchet.sh --update`
Verify: `quality/baseline.json` contains numerical non-null values for all non-optional metrics (`test_coverage`, `lint_warnings: 0`, `type_escape_hatches`, `dead_code: 0`, `dependency_count`).

- [x] **Step 4: Test that `./scripts/ratchet.sh` validates cleanly against the baseline**

Run: `./scripts/ratchet.sh`
Expected: Output confirms each metric is at or better than baseline, exiting 0.

- [ ] **Step 5: Commit**

```bash
git add quality/baseline.json scripts/ratchet.sh
git commit -m "feat(quality): configure quality baseline metrics and ratchet checker"
```

---

## Execution Handoff

Plan complete and saved to [docs/superpowers/plans/2026-11-19-optional-hardening-and-verification.md](file:///Users/pc/Documents/personal/fastapi_backend_boilerplate/docs/superpowers/plans/2026-11-19-optional-hardening-and-verification.md). Please review the plan. Which execution approach would you prefer?

- **Subagent-driven** - A fresh subagent implements each task and a fresh reviewer checks it before the next one starts, then a whole-branch review at the end. Most thorough; costs a fresh context per task and per review.
- **Native** - I implement every task myself in this session, the way this harness runs work, then one fresh reviewer on the most capable model checks the whole branch. Cheapest and fastest; no independent review until the end. Runs well with a mid-tier session model, since the plan carries the design.

For this plan I recommend **Native**, because these 4 tasks are focused scripting and integration verification steps with low coupling between them, and will execute swiftly and cleanly within our current environment. Does the plan capture what you want, and which approach should we use?
