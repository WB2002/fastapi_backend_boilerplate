#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "== scanning for unredacted long base64 literals in app/ =="

python3 - << 'EOF'
import os
import re
import sys

# Base64 string literal pattern with minimum 64 characters
BASE64_PATTERN = re.compile(r"[\"']([A-Za-z0-9+/]{64,}={0,2})[\"']")

# Known harmless constants or policy digests that can be safely allowed
ALLOWED_SUBSTRINGS: set[str] = set()

found_violations: list[tuple[str, int, str]] = []

for root, dirs, files in os.walk("app"):
    # Never scan hidden or cache dirs
    dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
    for file in files:
        if not file.endswith(".py"):
            continue
        filepath = os.path.join(root, file)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, start=1):
                    line_clean = line.strip()
                    if line_clean.startswith("#"):
                        continue
                    for match in BASE64_PATTERN.finditer(line):
                        literal = match.group(1)
                        if any(allowed in literal for allowed in ALLOWED_SUBSTRINGS):
                            continue
                        found_violations.append((filepath, line_idx, literal[:16] + "..." + literal[-8:]))
        except Exception as exc:
            print(f"Error reading {filepath}: {exc}", file=sys.stderr)
            sys.exit(1)

if found_violations:
    print(f"FAILED: Found {len(found_violations)} unallowed raw base64 literals in app/:", file=sys.stderr)
    for path, line, preview in found_violations:
        print(f"  {path}:{line} -> {preview}", file=sys.stderr)
    sys.exit(1)

print("base64 scan: OK")
EOF
