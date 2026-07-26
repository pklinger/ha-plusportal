# ha-plusportal

Unofficial Home Assistant integration for PlusPortal energy customer portals, plus the
standalone Python client it is built on.

## Layout

```
src/pyplusportal/              async client + CLI. Must never import homeassistant.
custom_components/plusportal/  the integration. Imports pyplusportal, not the reverse.
tests/                         library suite, offline, fixtures only
tests/test_live.py             opt-in, hits a real portal, credentials from env
tests_ha/                      integration suite, needs the HA test harness
docs/specs/                    numbered requirements, mechanically tied to tests
```

The dependency direction is one-way and load-bearing: the library is usable and testable
without Home Assistant, which is what the CLI exists to prove.

## Commands

```bash
uv run pytest                                  # library suite
uv run --group ha pytest -c pytest-ha.ini      # integration suite
uv run ruff check src tests tests_ha custom_components
uv run ruff format --check src tests tests_ha custom_components
uv run --group ha mypy                         # strict, runs on 3.13 (HA uses 3.13 syntax)
uv run pytest -m live                          # real portal; skips without .env
```

All five must pass before a commit. `uv run --group ha` is required for anything touching
`custom_components/` or `tests_ha/` — the plain venv has no Home Assistant.

The two suites run under separate pytest configs on purpose:
`pytest-homeassistant-custom-component` installs a global autouse fixture that breaks
pytest-asyncio's auto mode for plain async tests, so the library config disables that
plugin with `-p no:homeassistant`.

## What the portal does that will bite you

All three were established by reconciling against a live account. Each is silent when
wrong — the numbers stay plausible.

1. **The 15-minute series is `diagramType=power`, in kW.** The `consumption` series is
   daily only. Energy is `kW x interval length in hours`.
2. **Interval timestamps mark the END.** Asking for one day returns values from `00:15`
   through `00:00` the next day. Read as start times, the whole load profile shifts by a
   quarter hour.
3. **Only `period=month` returns local-midnight timestamps.** `day`, `week` and `year`
   return UTC midnight — one or two hours off in Germany. Always send `period=month`,
   whatever range you actually want.

Also required, or the portal refuses: `diagramSubType` (404 without it) and all four of
`allData`, `avgOnly`, `maxOnly`, `rawValues` on the power series (500 without them).

## Conventions

- **`Decimal` for anything that becomes money or is summed.** A year of quarter-hourly
  floats drifts visibly. JSON is decoded with `parse_float=Decimal`.
- **Only `W` and `E` readings are billable.** `V` is provisional and gets replaced later;
  counting it invents consumption the supplier never invoices.
- **Test-driven.** Write the failing test, watch it fail for the right reason, then
  implement. If a test passes the moment you write it, it is testing nothing — this repo
  has already shipped three assertions that could not fail. When in doubt, mutate the
  implementation and confirm the test breaks.
- **Never commit account data.** No meter numbers, customer numbers, portal user ids, real
  tenant numbers or utility names. Fixtures use fictional values; real ones come from the
  environment. A hook blocks commits that violate this.
- Docstrings in imperative mood, English, on every public function. Line length 100.

## Pull requests and versions

**`main` takes pull requests only.** Direct pushes are refused by a ruleset on GitHub and by
an agent hook before anything is sent. Work on a branch whose prefix states the change type,
because the required version bump is derived from it:

| Prefix | Version moves | Meaning |
|---|---|---|
| `fix/` | patch | a bug fix, nothing added or removed |
| `feat/` | minor | new behaviour, backwards compatible |
| `feat!/` | major | behaviour removed or changed incompatibly |
| `chore/` `docs/` `test/` `ci/` | none | nothing users receive changed |

```bash
git switch -c fix/statistics-sum-restarts
git push -u origin fix/statistics-sum-restarts
gh pr create --fill
```

A pull request touching `src/pyplusportal/` or `custom_components/plusportal/` must move the
version in **all three** places — `pyproject.toml`, the manifest's `version`, and the
manifest's requirements pin — or CI rejects it. Home Assistant caches an integration by its
manifest version and HACS compares it to offer updates, so shipping changed code under an
unchanged version leaves users on the old one with nothing to indicate why.

Below 1.0.0 a major bump moves the minor, as semver permits. Check locally with:

```bash
uv run python scripts/check_version_bump.py origin/main
```

## Specs

`docs/specs/` holds numbered requirements. Every one must be referenced by at least one
test, and every referenced id must exist — `tests/test_traceability.py` enforces both, so
a requirement cannot quietly lose its coverage and a test cannot cite a requirement that
was deleted.

Changing behaviour means changing the spec in the same commit. See
[docs/AGENTIC-SDLC.md](docs/AGENTIC-SDLC.md) for the loop this fits into.
