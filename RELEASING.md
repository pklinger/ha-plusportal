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

```bash
# 1. Bump all three in lockstep. The packaging tests fail if you miss one.
#    - pyproject.toml            [project] version
#    - custom_components/plusportal/manifest.json   version
#    - custom_components/plusportal/manifest.json   requirements pin

uv run pytest tests/test_packaging.py     # proves they agree
uv run pytest -q                          # library suite
uv run --group ha pytest -c pytest-ha.ini -q   # integration suite

git commit -am "Release 0.2.0"
git tag v0.2.0
git push origin main --tags
```

The `Release` workflow then verifies the tag matches the project version, runs both suites,
builds, publishes to PyPI and creates the GitHub release. A tag that disagrees with the code
fails before anything is published.

## Getting listed in HACS

Users can install straight away by adding the repository as a *custom repository* in HACS —
no listing required. To appear in the default HACS list:

1. Add the integration's brand images to
   [home-assistant/brands](https://github.com/home-assistant/brands). Use a neutral icon —
   do not submit Thüga SmartService's logo, which is their trademark and not ours to
   redistribute.
2. Open a pull request against
   [hacs/default](https://github.com/hacs/default) adding `pklinger/ha-plusportal` to
   `integration`.

Both require the repository to be public, to have a description and topics set, and to pass
the `hacs/action` and `hassfest` checks — the CI workflow already runs both on every push.
