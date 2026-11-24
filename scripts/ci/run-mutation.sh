#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "== running mutation testing (mutmut) =="

target_files="${1:-}"

if [ -z "$target_files" ] && [ -n "${QUALITY_BASE_REF:-}" ]; then
  target_files=$(git diff --name-only "${QUALITY_BASE_REF}...HEAD" 2>/dev/null | grep -E '^app/.*\.py$' || true)
fi

if [ -z "$target_files" ]; then
  target_files=$(git diff --name-only HEAD 2>/dev/null | grep -E '^app/.*\.py$' || true)
fi

if [ -z "$target_files" ]; then
  target_files=$(git status --porcelain 2>/dev/null | awk '{print $2}' | grep -E '^app/.*\.py$' || true)
fi

if [ -z "$target_files" ]; then
  echo "mutation testing: no modified source files under app/ to mutate; OK"
  exit 0
fi

echo "Mutating files: $target_files"
for f in $target_files; do
  if [ -f "$f" ]; then
    echo "Running mutmut on $f..."
    uv run mutmut run --paths-to-mutate "$f" || true
  fi
done

echo "mutation testing: OK"
