#!/usr/bin/env bash
# Cases the release-notes guard must judge correctly.
#
#   tests_harness/test_release_notes_guard.sh
#
# Mirrors test_push_guard.sh: a hole lets an undocumented release through, a
# false positive teaches people to bypass the guard instead of using it. Runs
# scripts/check_release_notes.py against a throwaway CHANGELOG.md so it never
# touches the project's real one.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

HOOK=.claude/hooks/no-release-without-notes.sh
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"; [ -f CHANGELOG.md.guard-test-real ] && mv CHANGELOG.md.guard-test-real CHANGELOG.md' EXIT

fail=0

check() {
  local expected="$1" desc="$2" command="$3" changelog="$4"
  printf '%s' "$changelog" >"$WORKDIR/CHANGELOG.md"
  cp CHANGELOG.md CHANGELOG.md.guard-test-real
  cp "$WORKDIR/CHANGELOG.md" CHANGELOG.md

  local payload got
  payload=$(printf '%s' "$command" |
    python3 -c 'import json,sys; print(json.dumps({"tool_input": {"command": sys.stdin.read()}}))')
  got=$(printf '%s' "$payload" | "$HOOK" >/dev/null 2>&1; echo $?)

  mv CHANGELOG.md.guard-test-real CHANGELOG.md

  if [ "$got" = "$expected" ]; then
    printf '  ok      %s\n' "$desc"
  else
    printf '  FAIL    %s (exit %s, expected %s)\n' "$desc" "$got" "$expected"
    fail=1
  fi
}

echo "release-notes guard:"

# v0.1.1 over the repo's real v0.1.0 tag is a patch move, so these exercise
# the "entry present/absent" plumbing without also invoking the
# major-equivalent Breaking-subsection rule; that rule has its own coverage in
# tests/test_release_notes.py.
FILLED="# Changelog

## [0.1.1] - 2026-01-01

### Added
- Something.
"

EMPTY="# Changelog

## [0.1.1] - 2026-01-01
"

check 2 "a tag with no changelog entry at all" \
  "git push origin v0.1.1" "$EMPTY"
check 2 "a tag whose entry is empty" \
  "git tag v0.1.1 && git push origin v0.1.1" "$EMPTY"
check 0 "a tag with a filled changelog entry" \
  "git push origin v0.1.1" "$FILLED"
check 0 "no tag mentioned at all" \
  "git push -u origin fix/x" "$FILLED"
check 0 "an unrelated command" \
  "ls -la" "$EMPTY"

exit $fail
