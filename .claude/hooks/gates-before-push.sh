#!/usr/bin/env bash
# Refuses `git push` when the gates CI runs are not green.
#
# The git pre-push hook already does this for pushes from a shell. This one
# covers pushes an agent makes, and gives it the failure output directly so it
# can fix the cause instead of retrying.
set -uo pipefail

payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)

case "$command" in
  (*"git push"*) ;;
  (*) exit 0 ;;
esac
# The pre-push git hook handles the shell path; skip when it is being bypassed
# deliberately, so --no-verify keeps meaning what it says.
case "$command" in (*--no-verify*) exit 0 ;; esac

cd "$(git rev-parse --show-toplevel)" 2>/dev/null || exit 0

if ! output=$(./.claude/hooks/gates.sh 2>&1); then
  {
    echo "Blocked: main must stay green, and the gates are not."
    echo
    echo "$output"
    echo
    echo "Fix these before pushing. Do not bypass with --no-verify unless the"
    echo "user has asked for a deliberately red push."
  } >&2
  exit 2
fi
exit 0
