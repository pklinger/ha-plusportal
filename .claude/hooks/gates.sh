#!/usr/bin/env bash
# Runs every gate CI runs. Used by the pre-push hook and the `verify` skill, so
# a red pipeline is discovered here rather than on GitHub.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

fail=0
run() {
  local label="$1"; shift
  if out=$("$@" 2>&1); then
    printf '  ok    %s\n' "$label"
  else
    printf '  FAIL  %s\n' "$label"
    printf '%s\n' "$out" | tail -15 | sed 's/^/        /'
    fail=1
  fi
}

echo "gates:"
run "ruff check"    uv run ruff check src tests tests_ha custom_components
run "ruff format"   uv run ruff format --check src tests tests_ha custom_components
run "mypy"          uv run --group ha mypy
run "library tests" uv run pytest -q
run "ha tests"      uv run --group ha pytest -c pytest-ha.ini -q
exit $fail
