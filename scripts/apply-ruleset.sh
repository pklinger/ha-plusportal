#!/usr/bin/env bash
# Applies .github/rulesets/main.json, creating it or updating it in place.
#
# Requires the repository to be public, or GitHub Pro; it fails with 403
# otherwise. Idempotent, so it can be re-run after editing the file — GitHub
# rejects a create when the ruleset name is already taken.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

repo="${1:-pklinger/ha-plusportal}"
file=.github/rulesets/main.json
name=$(python3 -c "import json; print(json.load(open('$file'))['name'])")

id=$(gh api "repos/$repo/rulesets" --jq ".[] | select(.name == \"$name\") | .id" 2>/dev/null || true)

if [ -n "$id" ]; then
  gh api "repos/$repo/rulesets/$id" -X PUT --input "$file" \
    --jq '"updated: \(.name) [\(.enforcement)]"'
else
  id=$(gh api "repos/$repo/rulesets" -X POST --input "$file" --jq '.id')
  echo "created: $name"
fi

echo "rules in force:"
gh api "repos/$repo/rulesets/$id" --jq '.rules[].type' | sed 's/^/  /'
echo "required checks:"
gh api "repos/$repo/rulesets/$id" \
  --jq '.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context' |
  sed 's/^/  /'
