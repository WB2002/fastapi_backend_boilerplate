#!/bin/bash
set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "== sync =="
uv sync --frozen

echo "== lint & format =="
uv run ruff check .
uv run ruff format --check .

echo "== type check =="
uv run mypy --strict app tests

echo "== test (unit) =="
uv run pytest tests/unit

echo "== test (property) =="
uv run pytest tests/property

echo "== test (contracts) =="
uv run pytest tests/contracts

echo "verify: OK"
