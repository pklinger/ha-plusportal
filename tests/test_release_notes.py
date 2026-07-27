"""Tests for scripts/check_release_notes.py.

Loaded by path: the script lives outside ``src/`` and is not part of the installed
package, the same way ``scripts/check_version_bump.py`` is only ever run directly.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_release_notes",
    Path(__file__).resolve().parents[1] / "scripts" / "check_release_notes.py",
)
assert _SPEC is not None and _SPEC.loader is not None
check_release_notes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_release_notes)


def test_a_missing_section_is_rejected() -> None:
    """PP-SEC-007: no entry for the version means the release cannot publish."""
    changelog = "# Changelog\n\n## [0.1.0] - 2026-07-26\n\n### Added\n- Initial release.\n"

    error = check_release_notes.check("0.2.0", changelog)

    assert error is not None
    assert "0.2.0" in error


def test_an_empty_section_is_rejected() -> None:
    """PP-SEC-007: a heading with nothing under it is not a release note."""
    changelog = "# Changelog\n\n## [0.2.0] - 2026-07-27\n\n## [0.1.0] - 2026-07-26\n\n- x\n"

    error = check_release_notes.check("0.2.0", changelog)

    assert error is not None
    assert "empty" in error


def test_a_non_empty_section_is_accepted() -> None:
    """PP-SEC-007: any non-empty body under the version heading is sufficient."""
    changelog = "# Changelog\n\n## [0.1.1] - 2026-07-27\n\n### Fixed\n- Something.\n"

    error = check_release_notes.check("0.1.1", changelog)

    assert error is None


def test_the_unreleased_section_does_not_satisfy_a_version() -> None:
    """An 'Unreleased' heading is not a version; renaming it is part of tagging."""
    changelog = "# Changelog\n\n## [Unreleased]\n\n### Added\n- Something.\n"

    error = check_release_notes.check("0.3.0", changelog)

    assert error is not None


@pytest.mark.parametrize(
    ("version", "previous", "expected"),
    [
        ("0.3.0", "v0.1.0", True),  # 0.x: minor moved 1 -> 3, counts as major-equivalent
        ("0.1.1", "v0.1.0", False),  # 0.x: patch only
        ("1.0.0", "v0.9.0", True),  # major moved 0 -> 1
        ("1.1.0", "v1.0.0", False),  # >=1.0: minor only, not major-equivalent
        ("2.0.0", "v1.5.0", True),  # major moved 1 -> 2
        ("0.1.0", None, False),  # first release ever: nothing to break
    ],
)
def test_major_equivalent_detection(version: str, previous: str | None, expected: bool) -> None:
    """PP-SEC-008: below 1.0.0 a minor move is treated as the major-equivalent bump."""
    assert check_release_notes.is_major_equivalent(version, previous) is expected


def test_a_major_equivalent_bump_needs_a_breaking_subsection(tmp_path: Path) -> None:
    """PP-SEC-008: silence in the changelog must not read as 'safe to update'."""
    _tag(tmp_path, "v0.1.0")
    changelog = "# Changelog\n\n## [0.3.0] - 2026-07-27\n\n### Added\n- Something new.\n"

    error = _check_in(tmp_path, "0.3.0", changelog)

    assert error is not None
    assert "Breaking" in error


def test_a_major_equivalent_bump_with_a_breaking_subsection_is_accepted(tmp_path: Path) -> None:
    """PP-SEC-008: a filled-in Breaking subsection satisfies the requirement."""
    _tag(tmp_path, "v0.1.0")
    changelog = (
        "# Changelog\n\n## [0.3.0] - 2026-07-27\n\n"
        "### Added\n- Something new.\n\n### Breaking\n- The old thing stops working.\n"
    )

    error = _check_in(tmp_path, "0.3.0", changelog)

    assert error is None


def test_a_breaking_heading_with_nothing_under_it_does_not_count(tmp_path: Path) -> None:
    """An empty '### Breaking' heading is indistinguishable from no heading at all."""
    _tag(tmp_path, "v0.1.0")
    changelog = "# Changelog\n\n## [0.3.0] - 2026-07-27\n\n### Added\n- x.\n\n### Breaking\n"

    error = _check_in(tmp_path, "0.3.0", changelog)

    assert error is not None


def test_a_patch_bump_does_not_need_a_breaking_subsection(tmp_path: Path) -> None:
    """PP-SEC-008 only applies to a major-equivalent move."""
    _tag(tmp_path, "v0.1.0")
    changelog = "# Changelog\n\n## [0.1.1] - 2026-07-27\n\n### Fixed\n- A bug.\n"

    error = _check_in(tmp_path, "0.1.1", changelog)

    assert error is None


def _tag(repo: Path, name: str) -> None:
    """Set up a throwaway git repo carrying one tag, for previous_tag() to read."""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=repo, check=True)
    subprocess.run(["git", "tag", name], cwd=repo, check=True)


def _check_in(repo: Path, version: str, changelog: str) -> str | None:
    """Run check() with ROOT pointed at ``repo``, so previous_tag() sees its tags."""
    original_root = check_release_notes.ROOT
    check_release_notes.ROOT = repo  # type: ignore[attr-defined]
    try:
        result: str | None = check_release_notes.check(version, changelog)
        return result
    finally:
        check_release_notes.ROOT = original_root  # type: ignore[attr-defined]
