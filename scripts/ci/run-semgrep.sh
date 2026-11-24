#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
export HOME=/tmp

echo "== running static security analysis (semgrep) =="

CONFIG="scripts/ci/semgrep-rules.yaml"

if [ "${SEMGREP_CONFIG:-}" = "auto" ]; then
  CONFIG="auto"
fi

if command -v semgrep >/dev/null 2>&1; then
  SEMGREP_CMD=(semgrep)
elif command -v uvx >/dev/null 2>&1; then
  SEMGREP_CMD=(uvx semgrep)
else
  SEMGREP_CMD=(uv tool run semgrep)
fi

"${SEMGREP_CMD[@]}" scan \
  --config "$CONFIG" \
  --error \
  --exclude 'tests' \
  app/

echo "semgrep: OK"
