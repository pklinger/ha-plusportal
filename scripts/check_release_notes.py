"""Refuse to release without a changelog entry for the version being tagged.

The release workflow (`.github/workflows/release.yml`) already ties the git tag to the
project version so a release can never ship code nobody agreed on; this ties the same tag
to a changelog entry so it can never ship without a human-readable account of what changed.
A major-equivalent version move — see CLAUDE.md for the below-1.0.0 exception — must also
carry a non-empty "Breaking" subsection, because a HACS user deciding whether to update has
nothing else to check that against.

PP-SEC-007, PP-SEC-008 in docs/specs/safety.md.

Run locally, or from the release workflow, with the version being released:

    uv run python scripts/check_release_notes.py 0.3.0
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"

#: A Keep a Changelog section heading: "## [X.Y.Z]" or "## [X.Y.Z] - 2026-07-27".
_SECTION_RE = re.compile(r"^## \[(?P<version>[^\]]+)\]")


def sections(changelog_text: str) -> dict[str, str]:
    """Map each changelog section's version heading to its body text."""
    lines = changelog_text.splitlines()
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if match:
            starts.append((match.group("version"), index))

    result: dict[str, str] = {}
    for position, (version, start) in enumerate(starts):
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        result[version] = "\n".join(lines[start + 1 : end]).strip()
    return result


def previous_tag(version: str) -> str | None:
    """The highest existing tag below ``version``, or ``None`` if there is none.

    Tags are compared as dotted integer tuples; a tag that does not parse that way is
    ignored rather than crashing a release over an unrelated stray tag.
    """

    def parse(tag: str) -> tuple[int, ...] | None:
        if not tag.startswith("v"):
            return None
        try:
            return tuple(int(part) for part in tag[1:].split("."))
        except ValueError:
            return None

    target = parse(f"v{version}")
    if target is None:
        return None

    result = subprocess.run(
        ["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    candidates = [parse(line) for line in result.stdout.splitlines()]
    lower = [tag for tag in candidates if tag is not None and tag < target]
    if not lower:
        return None
    return "v" + ".".join(str(part) for part in max(lower))


def is_major_equivalent(version: str, previous: str | None) -> bool:
    """Whether ``version`` is a major bump over ``previous`` (minor, below 1.0.0)."""
    if previous is None:
        return False

    def major_component(tag: str) -> tuple[int, int]:
        parts = [int(part) for part in tag.lstrip("v").split(".")]
        major, minor = parts[0], parts[1]
        return (0, minor) if major == 0 else (major, 0)

    return major_component(f"v{version}") != major_component(previous)


def check(version: str, changelog_text: str) -> str | None:
    """Return an error message, or ``None`` if the changelog satisfies the release."""
    found = sections(changelog_text)
    body = found.get(version)
    if body is None:
        return (
            f"No changelog entry for {version}.\n"
            f'Add a "## [{version}]" section to CHANGELOG.md describing what changed.'
        )
    if not body:
        return f"The changelog entry for {version} is empty."

    previous = previous_tag(version)
    if is_major_equivalent(version, previous):
        breaking = re.search(r"^### Breaking\s*$(.*?)(?=^### |\Z)", body, re.MULTILINE | re.DOTALL)
        if breaking is None or not breaking.group(1).strip():
            return (
                f"{version} is a major-equivalent version move"
                + (f" over {previous}" if previous else "")
                + f", but the CHANGELOG.md entry for {version} has no non-empty "
                '"### Breaking" subsection.\n'
                "State what breaks, or explain in it that nothing does."
            )
    return None


def main() -> int:
    """Check the changelog entry for the version given on the command line."""
    if len(sys.argv) != 2:
        print("usage: check_release_notes.py X.Y.Z", file=sys.stderr)
        return 2

    version = sys.argv[1]
    error = check(version, CHANGELOG.read_text(encoding="utf-8"))
    if error:
        print(f"::error::{error}")
        return 1

    print(f"CHANGELOG.md has a release note for {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
