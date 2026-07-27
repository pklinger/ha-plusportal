# PP-SEC — Privacy, secrets and packaging

### PP-SEC-001 — No real account data in the repository
No meter numbers, customer numbers, portal user ids, real tenant numbers or portal
hostnames. Fixtures use fictional values; real ones come from the environment.

*Why:* a meter number identifies a physical connection point and appears on the bill.
Combined with a named utility it narrows to a household. A commit hook enforces this, because
this project already had all three reach tracked files once.

### PP-SEC-002 — Credentials are never command line arguments
Only environment variables, optionally via a gitignored `.env`.

*Why:* arguments land in shell history and in the process list.

### PP-SEC-003 — Recorded fixtures are redacted
`probe` replaces session ids, usernames and meter numbers before writing.

*Why:* recording a real response is the most likely way real data enters the repository.

### PP-SEC-004 — Library version, manifest version and requirements pin agree
Enforced by tests; the release workflow adds the git tag to the chain.

*Why:* nothing at runtime notices a stale pin. HACS users would silently receive a library
version this repository never tested against.

### PP-SEC-005 — The library is pinned exactly, never by range
`pyplusportal==X.Y.Z`.

*Why:* a range lets Home Assistant install a version that was never tested here.

### PP-SEC-006 — Translations cover every declared key
`strings.json`, `en.json` and `de.json` carry identical key sets.

*Why:* a missing key renders as a raw identifier in the interface.

### PP-SEC-007 — A release never ships without a changelog entry
`CHANGELOG.md` must carry a section for the version being tagged, with non-empty content,
before the release workflow is allowed to publish.

*Why:* a HACS user decides whether to update from the release notes. No entry means no way
to know what changed short of reading the diff themselves.

### PP-SEC-008 — A major version bump states what breaks
When the tag moves the major version (or, below 1.0.0, the minor — see CLAUDE.md), the
changelog entry must contain a "Breaking" subsection with non-empty content.

*Why:* below 1.0.0 a minor bump can still remove or change behaviour incompatibly. Silence
in the changelog reads as "safe to update", which a breaking change is not.
