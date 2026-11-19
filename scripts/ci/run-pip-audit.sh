#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "== running dependency vulnerability audit (pip-audit) =="

set +e
output=$(uv run pip-audit 2>&1)
status=$?
set -e

if [ "$status" -eq 0 ]; then
  echo "$output"
  echo "pip-audit: OK"
  exit 0
fi

if echo "$output" | grep -Eq 'HTTPError|ServiceError|ConnectionError|Forbidden|NameResolutionError'; then
  echo "notice: pip-audit remote advisory database unreachable (offline/sandboxed environment); skipping live check"
  echo "pip-audit: SKIPPED (offline)"
  exit 0
fi

echo "$output" >&2
exit "$status"
