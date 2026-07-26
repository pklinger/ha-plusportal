---
name: verify
description: Use when a change to this repository needs verifying before commit or push — runs every gate CI runs, then exercises the affected layer for real. Also use when CI is red and you need to reproduce it locally.
---

# Verify

Green gates are necessary, not sufficient. The gates prove the code is consistent;
exercising it proves it does the right thing.

## 1. Gates — always

```bash
./.claude/hooks/gates.sh
```

ruff check, ruff format, mypy strict, both test suites. Exactly what CI runs, so a red
pipeline shows up here first. All five must pass; the pre-push hook refuses otherwise.

## 2. Exercise the layer you touched

**Client or CLI** — run it against the real portal. The offline suite uses recorded
fixtures, so it cannot catch a changed API:

```bash
uv run pytest -m live          # reconciles three figures against the portal
uv run pyplusportal overview
uv run pyplusportal readings --from <date> --to <date>
```

`-m live` skips silently without `.env`. If it skips, you have not verified anything —
say so rather than reporting success.

**Integration** — the HA suite covers logic, not appearance. For anything a user sees
(config flow wording, entity names, dashboard behaviour), load it in a real Home Assistant
and look. If you cannot, say which parts are unverified.

**Statistics** — the failure mode is a plausible-looking wrong graph. Check the numbers,
not just that rows were written.

## 3. When a test passes the moment you write it

Mutate the implementation and confirm the test fails. This repository has shipped three
assertions that could not fail: two matched text argparse prints anyway, one asserted a
value equals itself. Each was caught in review, not by the suite.

```bash
# stub the function the test targets, run just that test, expect red, restore
```

Report the mutation you tried and what happened. "Tests pass" is not evidence when the
test is new.

## 4. Report honestly

State what you ran, what passed, and what you did not verify. A skipped live suite, an
unexercised UI path and an untested error branch are all worth one sentence each.
