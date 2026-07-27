# The development loop

How work moves through this repository, and what stops an agent — or a person — from
shipping something wrong. The point is not that agents write the code; it is that every
claim they make is checkable by something other than their own say-so.

## The loop

```
issue  →  spec  →  failing test  →  code  →  gates  →  review  →  merge  →  release
           │           │                       │         │
           └───────────┴── traceability ───────┘         └── CI, all green
```

1. **Issue.** Describe the behaviour and why it matters. `@claude` in the issue body or a
   comment starts an agent on it.
2. **Spec.** New or changed behaviour gets a requirement in `docs/specs/` in the same
   change. Ids are permanent.
3. **Failing test first.** Write it, watch it fail for the right reason, then implement.
   A test that passes the moment it is written is testing nothing.
4. **Gates.** `./.claude/hooks/gates.sh` — ruff, format, mypy strict, both suites. The same
   five checks CI runs.
5. **Review.** Every pull request gets an automated review against the project's own
   standards. Advisory: it comments, the gates decide.
6. **Pull request.** Every change, without exception. The branch prefix states the change
   type and determines which part of the version moves; see CLAUDE.md.
7. **Merge, then release.** See `.claude/skills/release/SKILL.md`.

## What is enforced, and by what

| Rule | Enforced by | Where it runs |
|---|---|---|
| main takes pull requests only | GitHub ruleset, `.claude/hooks/no-direct-push-to-main.sh` | GitHub and agent |
| Shipped changes bump the version | `scripts/check_version_bump.py` | CI, on pull requests |
| Every requirement has a test; every cited requirement exists | `tests/test_traceability.py` | gates, CI |
| Versions in pyproject, manifest and the pin agree | `tests/test_packaging.py` | gates, CI |
| No account data in a commit | `.claude/hooks/no-account-data.sh` | agent, pre-commit |
| No push while the gates are red | `.githooks/pre-push`, `.claude/hooks/gates-before-push.sh` | shell and agent |
| Formatting | `.claude/hooks/format-python.sh` | after every file write |
| The guards themselves | `tests_harness/test_push_guard.sh` | gates |
| Manifest and HACS validity | hassfest, `hacs/action` | CI |

The guards are tested too. The push guard was rewritten after it refused a legitimate
command — it judged the whole compound line, so a `main` mentioned in an unrelated
`gh pr edit` blocked the push beside it. A guard that misfires gets worked around, which
is worse than not having it.

Three of these exist because the thing they prevent already happened here: account data
reached tracked files, a test asserted a value equals itself, and `hacs.json` carried a key
that invalidated the whole file.

### Green is not optional

Server-side enforcement — a required-status-checks ruleset on `main` — needs a public
repository or GitHub Pro, so it is not active yet. `.github/rulesets/main.json` holds it
ready and `scripts/apply-ruleset.sh` applies it. Until then the pre-push hooks are the
enforcement, and they are why `core.hooksPath` points at `.githooks`:

```bash
git config core.hooksPath .githooks   # once, after cloning
```

`--no-verify` exists and works. Using it means saying out loud that you are pushing
something red.

## Working with the agents

**In an issue or PR:** `@claude implement this`, `@claude why does this test pass?`. The
agent checks out the repository, so `CLAUDE.md`, `docs/specs/` and `.claude/` shape what it
does — this file is not decoration, it is the agent's brief.

**On every pull request:** an automated review runs unprompted. It weights spec compliance,
vacuous tests, the three portal traps and account-data leaks, in that order.

**Setup required:** the Claude GitHub App on the repository and an `ANTHROPIC_API_KEY`
secret. Without them the two Claude workflows fail rather than skip — they are opt-in by
existence, so delete them if you do not want them.

## What an agent may not decide alone

- Publishing anything: a PyPI release, making the repository public, pushing a tag.
- Widening permissions in `.claude/settings.json`.
- Rewriting history.
- Removing a requirement from `docs/specs/`. Changing one is normal; deleting one means the
  project no longer promises something, and that is the user's call.

## Reporting

State what was verified and what was not. A skipped live suite, an unexercised UI path, a
test that has never been seen to fail — each is worth one sentence. "Tests pass" is a claim
about the tests, not about the code.
