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

uv run semgrep scan \
  --config "$CONFIG" \
  --error \
  --exclude '.reference_nestjs' \
  --exclude 'tests' \
  app/

echo "semgrep: OK"
