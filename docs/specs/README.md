# Specs

Numbered requirements for what this project must do. They are not documentation of intent —
they are checked.

## The rule

Every requirement is referenced by at least one test, and every referenced id exists.
`tests/test_traceability.py` enforces both directions:

- A requirement nobody tests fails the suite. Coverage cannot quietly lapse.
- A test citing a deleted requirement fails the suite. Specs cannot rot behind the code.

## Referencing a requirement from a test

Put the id in the test's docstring, or in a comment on the test:

```python
def test_interval_timestamps_are_read_as_interval_ends():
    """PP-EXT-004: the portal labels a quarter hour by when it ended."""
```

## Id scheme

| Prefix | Area | File |
|---|---|---|
| `PP-EXT` | extraction — talking to the portal, parsing what it returns | [extraction.md](extraction.md) |
| `PP-COST` | cost and billing projection | [cost.md](cost.md) |
| `PP-HA` | the Home Assistant integration | [integration.md](integration.md) |
| `PP-SEC` | privacy, secrets and packaging | [safety.md](safety.md) |

Ids are permanent. Retire a requirement by deleting it and its tests in the same commit;
never renumber, or references in old commits and issues stop meaning anything.

## Writing one

State the behaviour, then why it matters. The rationale is the part that survives — it is
what stops someone "simplifying" the requirement away two years from now.
