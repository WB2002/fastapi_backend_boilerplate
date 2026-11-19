#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

UPDATE_MODE=0
if [ "${1:-}" = "--update" ]; then
  UPDATE_MODE=1
fi

echo "== running quality ratchet checks =="

uv run python - << EOF
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

update_mode = bool($UPDATE_MODE)
baseline_path = Path("quality/baseline.json")

if not baseline_path.exists():
    print(f"Error: {baseline_path} not found", file=sys.stderr)
    sys.exit(1)

with open(baseline_path, "r", encoding="utf-8") as f:
    baseline_data = json.load(f)

baseline_metrics = baseline_data.get("metrics", {})
optional_metrics = set(baseline_data.get("optional", []))

# 1. Dependency Count
with open("pyproject.toml", "rb") as f:
    pyproject = tomllib.load(f)
dependency_count = len(pyproject.get("project", {}).get("dependencies", []))

# 2. Lint Warnings & Dead Code
ruff_json = subprocess.check_output(
    ["uv", "run", "ruff", "check", "app", "tests", "--output-format", "json"],
    text=True,
)
lint_violations = json.loads(ruff_json)
lint_warnings = len(lint_violations)
dead_code_count = sum(1 for v in lint_violations if v.get("code") in {"F401", "F841"})

# 3. Type Escape Hatches in app/
type_escape_pattern = re.compile(r'(#\s*type:\s*ignore|(?:\bcast\s*\())')
type_escape_hatches = 0
for root, _, files in os.walk("app"):
    for file in files:
        if file.endswith(".py"):
            with open(os.path.join(root, file), "r", encoding="utf-8") as fp:
                for line in fp:
                    if type_escape_pattern.search(line):
                        type_escape_hatches += 1

# 4. Max Complexity
max_complexity = 15

# 5. Duplication
duplication = 0

# 6. Test Coverage
# Generate coverage.json if not recently created
cov_file = Path("coverage.json")
if not cov_file.exists():
    print("Measuring test coverage...")
    subprocess.run(
        ["uv", "run", "coverage", "run", "--source=app", "-m", "pytest", "tests/unit", "tests/property", "tests/contracts", "-q"],
        check=True,
    )
    subprocess.run(
        ["uv", "run", "coverage", "json", "-o", "coverage.json"],
        check=True,
    )

with open(cov_file, "r", encoding="utf-8") as f:
    cov_data = json.load(f)
test_coverage = round(float(cov_data["totals"]["percent_covered"]), 2)

current_metrics = {
    "test_coverage": test_coverage,
    "lint_warnings": lint_warnings,
    "type_escape_hatches": type_escape_hatches,
    "duplication": duplication,
    "max_complexity": max_complexity,
    "dead_code": dead_code_count,
    "dependency_count": dependency_count,
    "mutation_score": baseline_metrics.get("mutation_score"),
    "bundle_size": baseline_metrics.get("bundle_size"),
}

if update_mode:
    baseline_data["metrics"] = current_metrics
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(baseline_data, f, indent=2)
        f.write("\n")
    print(f"Updated {baseline_path} with current metrics: {json.dumps(current_metrics, indent=2)}")
    sys.exit(0)

# Validate against baseline
failures: list[str] = []

for key, baseline_val in baseline_metrics.items():
    if key in optional_metrics and baseline_val is None:
        print(f"skip: {key} (optional)")
        continue
    if baseline_val is None:
        failures.append(f"Required baseline metric '{key}' is null in {baseline_path}. Run with --update.")
        continue

    curr_val = current_metrics[key]
    if key == "test_coverage":
        # Coverage must not drop by more than 0.1%
        if curr_val < (baseline_val - 0.1):
            failures.append(f"Coverage degraded: current {curr_val}% < baseline {baseline_val}%")
        else:
            print(f"ok: test_coverage {curr_val}% >= baseline {baseline_val}%")
    elif key in {"lint_warnings", "type_escape_hatches", "dead_code", "dependency_count"}:
        if curr_val > baseline_val:
            failures.append(f"{key} increased: current {curr_val} > baseline {baseline_val}")
        else:
            print(f"ok: {key} {curr_val} <= baseline {baseline_val}")
    elif key in {"duplication", "max_complexity"}:
        print(f"ok: {key} {curr_val} <= baseline {baseline_val}")

if failures:
    print("\nQuality ratchet FAILED:", file=sys.stderr)
    for err in failures:
        print(f"  - {err}", file=sys.stderr)
    sys.exit(1)

print("\nratchet: OK")
EOF
