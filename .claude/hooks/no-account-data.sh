#!/usr/bin/env bash
# Blocks commits that would put real account data into the repository.
#
# This exists because it already happened: a meter number, a customer number
# and a portal user id reached tracked files, including — of all places — the
# test that proves the redaction helper works. They were caught by hand, one
# step before the repository went public. This hook is the part that does not
# depend on someone remembering.
#
# Reads a Claude Code PreToolUse payload on stdin. Exit 2 blocks the tool call
# and returns stderr to the model.
set -uo pipefail

payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)

# Only interested in commits. Everything else passes straight through.
case "$command" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

cd "$(git rev-parse --show-toplevel)" 2>/dev/null || exit 0
staged=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
[ -z "$staged" ] && exit 0

# Patterns for values that identify a real account or connection point.
# Deliberately shaped, not a list of known values: a new meter number must trip
# this too.
findings=$(printf '%s\n' "$staged" | while IFS= read -r file; do
  # Patterns are wrapped in balanced parens: inside $( ), an unbalanced ')'
  # from a case pattern terminates the substitution and breaks the parser.
  case "$file" in
    (uv.lock|*.png|*.lock) continue ;;                # hashes collide with digit runs
    (.claude/hooks/no-account-data.sh) continue ;;    # this file describes the patterns
    (docs/specs/safety.md) continue ;;                # so does the spec
  esac
  [ -f "$file" ] || continue

  git diff --cached -U0 -- "$file" | grep '^+' | grep -v '^+++' | \
    grep -nEi \
      -e '[0-9][A-Z]{3}[0-9]{10}' \
      -e '\b1[0-9]{9}\b' \
      -e '\b0003[0-9]{2}\b' \
      -e 'plusportal\.de' \
      -e 'PLUSPORTAL_PASSWORD=.+' \
    | grep -viE '123456|1ABC0000000000|1XYZ9876543210|1000000000|<tenant>' \
    | sed "s|^|  $file: |"
done)

if [ -n "$findings" ]; then
  cat >&2 <<EOF
Blocked: the staged changes look like they contain real account data.

$findings

Nothing identifying a real account belongs in this repository — no meter
numbers, customer numbers, portal user ids, real tenant numbers or portal
hostnames. Use fictional values (tenant 123456, meter 1ABC0000000000*,
user 1000000000) and take real ones from the environment; see
docs/specs/safety.md.

If this is a false positive, say so and the check can be narrowed. Do not
work around it by unstaging the file and committing it another way.
EOF
  exit 2
fi

exit 0
