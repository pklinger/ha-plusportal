"""Keeps `docs/specs/` and the test suites honest about each other.

Two failure modes this catches, both silent otherwise:

- A requirement nobody tests. Coverage lapses the moment someone deletes the
  last test for it, and nothing says so.
- A test citing a requirement that no longer exists. Specs rot behind the code
  and the reference becomes a lie.

Both suites are scanned as text rather than by collecting markers, because they
run under separate pytest configurations and cannot see each other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "docs" / "specs"
TEST_DIRS = (ROOT / "tests", ROOT / "tests_ha")

#: `### PP-EXT-004 — title` in a spec file.
SPEC_HEADING = re.compile(r"^###\s+(PP-[A-Z]+-\d{3})\b", re.MULTILINE)
#: Any requirement id mentioned anywhere in a test file.
SPEC_REFERENCE = re.compile(r"\bPP-[A-Z]+-\d{3}\b")


def declared_requirements() -> dict[str, Path]:
    """Every requirement id, mapped to the spec file that declares it."""
    found: dict[str, Path] = {}
    for spec in sorted(SPEC_DIR.glob("*.md")):
        for match in SPEC_HEADING.finditer(spec.read_text(encoding="utf-8")):
            found[match.group(1)] = spec
    return found


def referenced_requirements() -> dict[str, set[Path]]:
    """Every requirement id cited by a test, mapped to the files citing it."""
    found: dict[str, set[Path]] = {}
    for directory in TEST_DIRS:
        for path in sorted(directory.rglob("*.py")):
            if path.name == Path(__file__).name:
                continue
            for match in SPEC_REFERENCE.finditer(path.read_text(encoding="utf-8")):
                found.setdefault(match.group(0), set()).add(path)
    return found


def test_the_specs_declare_requirements_at_all() -> None:
    """Guards the parser itself: an empty result would make the rest vacuous."""
    assert len(declared_requirements()) >= 20


def test_the_tests_reference_requirements_at_all() -> None:
    assert len(referenced_requirements()) >= 20


def test_every_requirement_is_covered_by_a_test() -> None:
    uncovered = sorted(set(declared_requirements()) - set(referenced_requirements()))

    assert not uncovered, (
        "these requirements have no test referencing them:\n  "
        + "\n  ".join(uncovered)
        + "\n\nAdd the id to the docstring of the test that covers it, or delete "
        "the requirement if it no longer holds."
    )


def test_every_referenced_requirement_exists() -> None:
    declared = declared_requirements()
    dangling = {
        requirement: files
        for requirement, files in referenced_requirements().items()
        if requirement not in declared
    }

    assert not dangling, "tests cite requirements that no spec declares: " + ", ".join(
        f"{req} ({', '.join(p.name for p in sorted(files))})"
        for req, files in sorted(dangling.items())
    )


def test_requirement_ids_are_unique_across_spec_files() -> None:
    """Ids are permanent references; two meanings for one id breaks that."""
    seen: dict[str, Path] = {}
    duplicates: list[str] = []
    for spec in sorted(SPEC_DIR.glob("*.md")):
        for match in SPEC_HEADING.finditer(spec.read_text(encoding="utf-8")):
            requirement = match.group(1)
            if requirement in seen:
                duplicates.append(f"{requirement} in {seen[requirement].name} and {spec.name}")
            seen[requirement] = spec

    assert not duplicates, "duplicate requirement ids: " + "; ".join(duplicates)


@pytest.mark.parametrize("area", ["PP-EXT", "PP-COST", "PP-HA", "PP-SEC"])
def test_each_area_has_requirements(area: str) -> None:
    """A whole area silently losing its spec file would otherwise pass."""
    assert any(req.startswith(area) for req in declared_requirements()), (
        f"no requirements declared for {area}"
    )
