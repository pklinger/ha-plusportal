#!/usr/bin/env bash
# Formats and auto-fixes a Python file right after it is written, so the
# `ruff format --check` gate never fails for something a tool can fix.
set -uo pipefail

payload=$(cat)
file=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)

case "$file" in (*.py) ;; (*) exit 0 ;; esac
[ -f "$file" ] || exit 0

cd "$(git rev-parse --show-toplevel)" 2>/dev/null || exit 0
uv run ruff check --fix -q "$file" >/dev/null 2>&1
uv run ruff format -q "$file" >/dev/null 2>&1
exit 0
