"""Guards against the two versions in this repo drifting apart.

The library ships to PyPI, and the Home Assistant integration installs it from
there by an exact pin. Three places therefore have to agree, and nothing at
runtime would notice if they stopped: a wrong pin means HACS users silently get
a different library than the one this repo was tested against.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "plusportal" / "manifest.json"
PYPROJECT = ROOT / "pyproject.toml"

DISTRIBUTION = "pyplusportal"


@pytest.fixture
def pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


@pytest.fixture
def manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return loaded


def test_the_integration_version_matches_the_library_version(pyproject, manifest):
    """HACS shows the manifest version; a mismatch misreports what is installed."""
    assert manifest["version"] == pyproject["project"]["version"]


def test_the_integration_pins_the_library_version_it_was_built_against(pyproject, manifest):
    expected = f"{DISTRIBUTION}=={pyproject['project']['version']}"

    assert expected in manifest["requirements"]


def test_the_library_is_pinned_exactly_rather_than_by_range(manifest):
    """A range would let Home Assistant install a version never tested here."""
    pins = [req for req in manifest["requirements"] if req.startswith(DISTRIBUTION)]

    assert pins, "the integration must depend on the library"
    for pin in pins:
        assert "==" in pin, f"{pin!r} is not an exact pin"


def test_the_manifest_carries_every_key_hacs_requires(manifest):
    required = {
        "domain",
        "name",
        "codeowners",
        "documentation",
        "issue_tracker",
        "version",
    }

    assert required <= manifest.keys()


def test_the_domain_matches_the_directory_it_lives_in(manifest):
    assert manifest["domain"] == MANIFEST.parent.name


def test_the_version_is_semver_because_hacs_compares_releases(manifest):
    major, minor, patch = manifest["version"].split(".")

    assert all(part.isdigit() for part in (major, minor, patch))


def test_translations_cover_every_key_the_ui_declares():
    """A missing key shows a raw identifier in the Home Assistant interface."""
    base = json.loads((MANIFEST.parent / "strings.json").read_text(encoding="utf-8"))

    def leaves(node: dict[str, Any], prefix: str = "") -> set[str]:
        found: set[str] = set()
        for key, value in node.items():
            path = f"{prefix}.{key}"
            found |= leaves(value, path) if isinstance(value, dict) else {path}
        return found

    for language in ("en", "de"):
        translation = json.loads(
            (MANIFEST.parent / "translations" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert leaves(translation) == leaves(base), f"{language}.json is out of sync"
