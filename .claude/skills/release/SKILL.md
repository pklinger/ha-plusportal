---
name: release
description: Use when cutting a release of this project — bumping the version, publishing pyplusportal to PyPI and shipping the integration through HACS.
---

# Release

Two artefacts move together: the library on PyPI and the integration that pins it. Three
places carry the version and `tests/test_packaging.py` ties them to each other; the release
workflow adds the git tag to the same chain, so a mismatch fails before anything publishes.

## Bump

All three, in one commit:

- `pyproject.toml` → `[project] version`
- `custom_components/plusportal/manifest.json` → `version`
- `custom_components/plusportal/manifest.json` → `requirements` pin

```bash
uv run pytest tests/test_packaging.py    # proves they agree
./.claude/hooks/gates.sh                 # everything else
```

## Write the changelog entry

Rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] - YYYY-MM-DD` and fill it in. If
the version move is major-equivalent (a major bump, or below 1.0.0 a minor bump — see
CLAUDE.md), it must include a `### Breaking` subsection, non-empty, saying what breaks or
that nothing does. `scripts/check_release_notes.py X.Y.Z` checks this locally; the release
workflow and an agent hook both refuse a tag that fails it — PP-SEC-007, PP-SEC-008.

## Tag

```bash
git commit -am "Release X.Y.Z"
git tag vX.Y.Z
git push origin main --tags
```

The workflow verifies the tag matches the project version, verifies the changelog entry,
runs both suites, builds, publishes to PyPI through Trusted Publishing and creates the
GitHub release with that changelog entry as its notes. No API token is stored anywhere; see
RELEASING.md for the one-time PyPI setup.

## Before the first public release

- The repository must be public, or HACS validation cannot fetch `hacs.json` and
  `manifest.json` — it fetches them unauthenticated.
- Apply the branch ruleset once that is true: `scripts/apply-ruleset.sh`.
- `pyplusportal` must still be free on PyPI.

## Do not

Publish on the user's behalf without being asked. Tagging is theirs to trigger; a release
is public and cannot be unpublished from PyPI.
