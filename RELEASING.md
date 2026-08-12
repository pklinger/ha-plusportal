# Releasing

Two artefacts ship from this repo and they must move together: the library goes to PyPI as
`pyplusportal`, and the Home Assistant integration installs exactly that version by pin.
`tests/test_packaging.py` enforces that `pyproject.toml`, `manifest.json`'s `version` and
its `requirements` pin all agree; the release workflow adds the git tag to that chain.

## One-time setup

1. **Create the GitHub repository** as `pklinger/ha-plusportal` and push `main`.
2. **Claim the PyPI name.** `pyplusportal` must be free on PyPI. Check
   <https://pypi.org/project/pyplusportal/> before the first release.
3. **Configure Trusted Publishing** so no API token is ever stored in the repo. On PyPI,
   under the project's *Publishing* settings, add a GitHub publisher:

   | Field | Value |
   |---|---|
   | Owner | `pklinger` |
   | Repository | `ha-plusportal` |
   | Workflow | `release.yml` |
   | Environment | `pypi` |

   For a project that does not exist on PyPI yet, add it as a *pending* publisher instead —
   same fields, plus the project name `pyplusportal`.
4. **Create the `pypi` environment** in the GitHub repository settings. Requiring a reviewer
   on it is a cheap safety net: nothing reaches PyPI without a deliberate approval.

## Cutting a release

The version normally moves in the same pull request as the change that earns it — see the
branch-prefix table in CLAUDE.md. A release that carries no code change of its own still
needs a `fix/` or `feat/` branch, because touching `manifest.json` is what the version gate
watches for and a `chore/` prefix makes it refuse.

```bash
git switch -c fix/whatever-this-release-fixes

# 1. Bump all four in lockstep. The packaging tests fail if you miss one.
#    - pyproject.toml            [project] version
#    - custom_components/plusportal/manifest.json   version
#    - custom_components/plusportal/manifest.json   requirements pin
#    - uv.lock                   via `uv lock`

# 2. Add the section for this version to CHANGELOG.md. Without it the release
#    workflow refuses to publish (PP-SEC-007).

.claude/hooks/gates.sh                                  # every gate CI runs
uv run python scripts/check_version_bump.py origin/main # after committing
uv run python scripts/check_release_notes.py 0.2.0

git push -u origin fix/whatever-this-release-fixes
gh pr create --fill
```

Once that pull request is merged, tag the merge commit:

```bash
git switch main && git pull --ff-only
git tag -a v0.2.0 -m "Release 0.2.0"
```

The tag has to be pushed from somewhere other than `main`. The push guard refuses any push
made while standing on the protected branch — it does not try to tell a tag refspec from a
branch one, on the grounds that a guard that parses arguments grows holes. So:

```bash
git switch -c release/v0.2.0    # local only, never pushed
git push origin v0.2.0
```

The `Release` workflow then verifies the tag matches the project version, runs both suites,
builds, publishes to PyPI and creates the GitHub release. A tag that disagrees with the code
fails before anything is published.

Building and publishing happen in one job on purpose. Passing `dist/` between jobs needs two
more actions, and this workflow gets exactly one attempt per tag — a version cannot be
re-published to PyPI.

## Getting listed in HACS

Users can install straight away by adding the repository as a *custom repository* in HACS —
no listing required. To appear in the HACS store's own search, the repository has to be in
the default catalogue: [hacs/default#9925](https://github.com/hacs/default/pull/9925) asks
for that and is waiting in a queue HACS itself measures in months.

What that submission needed, all of it already true here: a public repository with a
description, topics and issues enabled, at least one release, `hacs.json` with a `name`, a
manifest carrying `domain`, `documentation`, `issue_tracker`, `codeowners`, `name` and
`version`, and green `hacs/action` and `hassfest` runs — CI runs both on every push. Two
that are easy to miss:

- **`country` in the released `hacs.json`.** HACS asks repositories with a limited audience
  to declare it, and reads the key from the release rather than from `main` (PP-SEC-010).
- **Brand assets.** `custom_components/plusportal/brand/icon.png` is what HACS checks for,
  256×256 with a 512×512 `icon@2x.png` beside it. Do not submit anything to
  [home-assistant/brands](https://github.com/home-assistant/brands): since Home Assistant
  2026.3 custom integrations serve their own icons, and a bot closes every pull request
  adding a new `custom_integrations/` folder. Keep the icon neutral either way — Thüga
  SmartService's logo is their trademark and not ours to redistribute.

One consequence to expect: the HACS store list fetches icons from the brands CDN, so the
entry shows the grey "logo missing" placeholder there no matter what the repository ships.
The real icon appears once the integration is installed, on Home Assistant 2026.3 or newer.
