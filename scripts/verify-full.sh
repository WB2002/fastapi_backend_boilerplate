#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "== 1. standard verification =="
./scripts/verify.sh

echo "== 2. documentation & skill checks =="
./scripts/check-docs.sh
./scripts/check-skills.sh

echo "== 3. security & ci scanners =="
./scripts/ci/scan-long-base64-literals.sh
./scripts/ci/run-pip-audit.sh
./scripts/ci/run-semgrep.sh
./scripts/ci/run-mutation.sh

echo "== 4. quality ratchet =="
./scripts/ratchet.sh

echo "== 5. integration test suite =="
if command -v docker >/dev/null 2>&1 && (docker info >/dev/null 2>&1 || [ -S /var/run/docker.sock ]); then
  uv run pytest tests/integration
else
  echo "notice: Docker unavailable; skipping live container integration suite"
fi

echo "verify-full: OK"
