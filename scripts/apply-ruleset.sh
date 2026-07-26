#!/usr/bin/env bash
# Requires main to pass CI before anything lands. Needs the repository to be
# public (or GitHub Pro); it fails with a 403 otherwise.
set -euo pipefail
repo="${1:-pklinger/ha-plusportal}"
gh api "repos/$repo/rulesets" -X POST \
  --input "$(git rev-parse --show-toplevel)/.github/rulesets/main.json" \
  --jq '"applied: \(.name) [\(.enforcement)]"'
