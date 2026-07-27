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

# Look at the push invocation alone, not the rest of a compound command.
# Judging the whole string blocked pushing a feature branch when an unrelated
# `gh pr edit --base ...` followed it, and blocked writing files that merely
# describe this guard.
push=$(printf '%s' "$command" | sed -n 's/.*\(git[[:space:]][[:space:]]*push[^;&|]*\).*/\1/p')
[ -z "$push" ] && exit 0

cd "$(git rev-parse --show-toplevel)" 2>/dev/null || exit 0
branch="${PUSH_GUARD_BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null)}"

# Deliberately blunt beyond that: any push while standing on the protected
# branch, and any push naming it. Telling a flag from a refspec is where a
# guard grows a hole, so it does not try — switch to the branch you mean.
targets_main=0
[ "$branch" = "main" ] && targets_main=1
case "$push" in (*" main"*|*":main"*|*"HEAD:main"*) targets_main=1 ;; esac
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
