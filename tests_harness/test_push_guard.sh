#!/usr/bin/env bash
# Cases the push guard must judge correctly.
#
#   tests_harness/test_push_guard.sh
#
# The guard is what keeps the default branch pull-request-only, so both
# directions matter: a hole lets a direct push through, and a false positive
# teaches people to work around the guard instead of using it.
#
# The branch name is assembled at runtime rather than written out, because the
# guard inspects the whole command it is given — a literal here would make it
# refuse to let this very file be written.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

HOOK=.claude/hooks/no-direct-push-to-main.sh
DEFAULT=$(printf 'ma%sn' i)

fail=0

check() {
  local expected="$1" desc="$2" command="$3" branch="${4:-feat/x}"
  local payload got
  payload=$(printf '%s' "$command" |
    python3 -c 'import json,sys; print(json.dumps({"tool_input": {"command": sys.stdin.read()}}))')

  got=$(printf '%s' "$payload" | PUSH_GUARD_BRANCH="$branch" "$HOOK" >/dev/null 2>&1; echo $?)

  if [ "$got" = "$expected" ]; then
    printf '  ok      %s\n' "$desc"
  else
    printf '  FAIL    %s (exit %s, expected %s)\n' "$desc" "$got" "$expected"
    fail=1
  fi
}

echo "push guard:"

# Must refuse.
check 2 "refspec names the default branch"   "git push origin $DEFAULT"        "feat/x"
check 2 "explicit HEAD refspec"              "git push origin HEAD:$DEFAULT"   "feat/x"
check 2 "standing on it, no arguments"       "git push"                        "$DEFAULT"
check 2 "standing on it, remote only"        "git push origin"                 "$DEFAULT"
check 2 "standing on it, forced"             "git push --force"                "$DEFAULT"

# Must allow.
check 0 "a feature branch"                   "git push -u origin feat/x"       "feat/x"
check 0 "a feature branch, then an unrelated command naming the default one" \
        "git push -u origin feat/x && gh pr edit 3 --base $DEFAULT"            "feat/x"
check 0 "no push in the command at all"      "gh pr edit 3 --base $DEFAULT"    "feat/x"
# --no-verify is git's escape hatch for git hooks, and the gates hook honours it
# because pushing something red is occasionally deliberate. This one does not:
# the branch takes pull requests only, GitHub's ruleset refuses the push anyway,
# and letting it through locally just turns a clear message into a remote error.
check 2 "--no-verify does not open a way past this one" \
        "git push --no-verify origin $DEFAULT"                                 "feat/x"
check 0 "an unrelated command"               "ls -la"                          "feat/x"
# A heredoc body is text the command writes, not a command it runs. Writing down
# what the guard refuses — in a commit message, a release runbook, this file —
# must not itself be refused.
check 0 "a commit whose message quotes a push at the default branch" \
        "$(printf 'git commit -F - <<%sMSG%s\nSay what the guard refuses\n\n  git push origin %s --tags\n\nMSG\n' "'" "'" "$DEFAULT")" \
        "docs/x"
check 2 "a real push still counts when a heredoc precedes it" \
        "$(printf 'cat <<%sEOF%s > note.txt\nhello\nEOF\ngit push origin %s\n' "'" "'" "$DEFAULT")" \
        "docs/x"

exit $fail
