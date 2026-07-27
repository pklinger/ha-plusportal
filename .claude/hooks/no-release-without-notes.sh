#!/usr/bin/env bash
# Refuses to push a release tag that CHANGELOG.md has no entry for.
#
# The release workflow (scripts/check_release_notes.py, PP-SEC-007/PP-SEC-008)
# checks this in CI, but CI runs after the tag is already public — pushing a
# tag cannot be undone the way an ordinary commit can. This stops it before it
# leaves the machine, the same way no-direct-push-to-main.sh does for main.
set -uo pipefail

payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)

case "$command" in
  (*"git push"*) ;;
  (*) exit 0 ;;
esac

cd "$(git rev-parse --show-toplevel)" 2>/dev/null || exit 0

# Versions named explicitly in the command, e.g. `git push origin v0.3.0` or a
# `git tag v0.3.0` earlier in the same compound line.
versions=$(printf '%s' "$command" | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | sort -u)

# `--tags` with no version named explicitly: treat every local tag the remote
# does not have yet as about to be pushed.
if [ -z "$versions" ] && printf '%s' "$command" | grep -q -- '--tags'; then
  while IFS= read -r tag; do
    [ -z "$tag" ] && continue
    if ! git ls-remote --exit-code --tags origin "$tag" >/dev/null 2>&1; then
      versions="$versions
$tag"
    fi
  done <<EOF
$(git tag --list 'v*')
EOF
  versions=$(printf '%s' "$versions" | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | sort -u)
fi

[ -z "$versions" ] && exit 0

fail=0
while IFS= read -r tag; do
  [ -z "$tag" ] && continue
  version="${tag#v}"
  if ! output=$(uv run python scripts/check_release_notes.py "$version" 2>&1); then
    echo "$output" >&2
    fail=1
  fi
done <<EOF
$versions
EOF

if [ "$fail" != "0" ]; then
  cat >&2 <<EOF

Blocked: a release must ship with a changelog entry (PP-SEC-007, PP-SEC-008).
Add it to CHANGELOG.md, in the same commit as the version bump, before tagging.
See docs/AGENTIC-SDLC.md.
EOF
  exit 2
fi
exit 0
