# Plan: discoverable command line help for `pyplusportal`

## Context

The CLI works but is hard to discover. Running `pyplusportal` with no arguments prints a
two-line argparse usage error and exits 2 — it tells you that you did something wrong,
not what the tool can do. There is no `--version`, no examples, and nothing documents the
environment variables the tool depends on or the exit codes it returns. Someone who
installs the package has no way to learn its capabilities except reading the source.

Goal: a bare invocation, `--help`, `help` and `?` all lead somewhere useful, and the help
text itself answers "what can this do, how do I configure it, what do the exit codes mean".

## Global Constraints

These bind every task. A reviewer should treat a violation as a defect.

1. **Tests first.** Every behaviour change starts with a failing test in `tests/test_cli.py`.
   The existing 146 tests must keep passing.
2. **No new runtime dependencies.** Standard library only (`argparse`, `difflib`,
   `importlib.metadata`). The package's only runtime dependency stays `httpx`, because the
   Home Assistant integration installs this library from PyPI.
3. **Help goes to stdout and exits 0.** Errors go to stderr and exit non-zero. A user asking
   for help has not made a mistake.
4. **Exit codes are already defined** in `src/pyplusportal/cli.py` and must not change:
   `EXIT_OK = 0`, `EXIT_USAGE = 2`, `EXIT_UNAVAILABLE = 3`.
5. **`ruff check src tests` and `mypy` (strict) stay clean.** Line length 100. Docstrings in
   imperative mood (ruff rule D401), English, on every public function.
6. **No tracebacks reach the user** for any expected condition.
7. Credentials must never be accepted as command line arguments, and must never appear in
   help text or error output.

## Task 1 — A bare invocation and `help` / `?` lead to the help text

**File:** `src/pyplusportal/cli.py`, tests in `tests/test_cli.py`.

Required behaviour:

| Invocation | Result | Exit |
|---|---|---|
| `pyplusportal` (no arguments) | full help on **stdout** | 0 |
| `pyplusportal help` | full help on stdout | 0 |
| `pyplusportal ?` | full help on stdout | 0 |
| `pyplusportal --help` | full help on stdout (unchanged argparse behaviour) | 0 |
| `pyplusportal help readings` | the `readings` sub-command help on stdout | 0 |
| `pyplusportal ? cost` | the `cost` sub-command help on stdout | 0 |
| `pyplusportal help nonsense` | error on stderr naming the valid commands | 2 |
| `pyplusportal meter` (typo) | error on stderr: unknown command, suggesting `meters` | 2 |
| `pyplusportal frobnicate` (no near match) | error on stderr listing all valid commands | 2 |

Notes for the implementer:

- The sub-parser is currently created with `required=True`, which is what produces the
  terse error on a bare invocation. Removing that requirement and handling the empty case
  explicitly is the cleanest route; whatever you choose, all rows of the table must hold.
- Use `difflib.get_close_matches` for the suggestion. One suggestion is enough; if it
  returns several, list them.
- `help` and `?` must be reachable without being advertised as sub-commands that take part
  in normal parsing — do not let them appear as `{meters,overview,readings,cost,probe,help,?}`
  in the usage line, which would read as if `?` were an operation on the portal. Intercept
  them before `parse_args`.
- Add a short note in the main help that `?` may need quoting in some shells, since it is a
  glob character. Do not try to work around the shell.
- `main(argv)` keeps its signature and keeps returning an `int`; tests call it directly.

## Task 2 — Help text that answers what the tool can do

**File:** `src/pyplusportal/cli.py`, tests in `tests/test_cli.py`.

Required behaviour:

- `pyplusportal --version` and `pyplusportal -V` print the package version and exit 0.
  Read it with `importlib.metadata.version("pyplusportal")`; fall back to `"unknown"` if the
  package is not installed rather than raising.
- The main help gains an epilog containing, in this order:
  1. **Examples** — at least one worked example per sub-command, using real-looking values
     (tenant `123456`, dates in 2026).
  2. **Environment variables** — every variable the CLI reads, each with a one-line purpose:
     `PLUSPORTAL_TENANT`, `PLUSPORTAL_BASE_URL`, `PLUSPORTAL_USERNAME`, `PLUSPORTAL_PASSWORD`,
     `PLUSPORTAL_ENERGY_PRICE_CT`, `PLUSPORTAL_BASE_PRICE_EUR`, `PLUSPORTAL_MONTHLY_ADVANCE_EUR`,
     `PLUSPORTAL_BILLING_YEAR_START`, `PLUSPORTAL_NO_DOTENV`. State that a `.env` file in the
     working directory is read, and that the real environment takes precedence over it.
  3. **Exit codes** — 0 success, 2 usage or authentication problem, 3 portal unreachable.
- Every sub-parser gains a `description` explaining what it does and, where it clarifies
  usage, an epilog with an example. `cost` must state that the portal supplies no tariff
  data, so prices come from the environment.
- Use `argparse.RawDescriptionHelpFormatter` so the epilog layout survives.
- The `--format` option of `readings` currently has no `help`; give it one. Audit the other
  options for missing or unclear `help` text while you are there.

Verification for the implementer: `pyplusportal --help` must mention every environment
variable listed above. Assert that in a test rather than eyeballing it — a variable added
later without a help entry should fail the suite.

## Task 3 — Document the CLI surface in the README

**File:** `README.md`.

The README's "Quick start" shows three commands and predates `cost`. Replace it with a
section that lists every sub-command with a one-line purpose and a worked example, points
at `pyplusportal help` for details, and documents the tariff environment variables needed
for `cost`. Keep it short — the exhaustive reference lives in `--help`, and two copies of
the same list will drift apart.

Do not restate the API reverse-engineering findings; they belong in
`docs/superpowers/specs/2026-07-25-plusportal-ha-integration-design.md`.

## Verification

```bash
uv run pytest -q                 # all tests, including the new ones
uv run ruff check src tests
uv run mypy
uv run pyplusportal              # help, exit 0
uv run pyplusportal help cost
uv run pyplusportal --version
uv run pyplusportal meter        # suggests "meters", exit 2
```
