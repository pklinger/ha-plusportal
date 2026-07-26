#!/usr/bin/env bash
# Refuses a direct push to main. Every change arrives as a pull request.
#
# GitHub enforces this too — the ruleset on main requires one — but its refusal
# comes after the push has been attempted and reads like a permissions error.
# This says what to do instead, before anything is sent.
set -uo pipefail

payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)

case "$command" in
  (*"git push"*) ;;
  (*) exit 0 ;;
esac

cd "$(git rev-parse --show-toplevel)" 2>/dev/null || exit 0
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

# Pushing main, either by being on it or by naming it as the refspec.
targets_main=0
[ "$branch" = "main" ] && targets_main=1
case "$command" in (*" main"*|*":main"*|*"HEAD:main"*) targets_main=1 ;; esac
[ "$targets_main" = "0" ] && exit 0

cat >&2 <<EOF
Blocked: main takes pull requests only.

Work on a branch and open a pull request instead:

  git switch -c <type>/<short-description>
  git push -u origin <branch>
  gh pr create --fill

Branch names follow the change type — feat/, fix/, chore/, docs/, refactor/ —
because the release notes and the version bump are derived from it. See
docs/AGENTIC-SDLC.md.
EOF
exit 2
