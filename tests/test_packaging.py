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
HACS_JSON = ROOT / "hacs.json"

DISTRIBUTION = "pyplusportal"

#: Every key HACS accepts in `hacs.json`, mirrored from HACS_MANIFEST_JSON_SCHEMA
#: in hacs/integration. That schema is PREVENT_EXTRA: an unknown key is not
#: ignored, it invalidates the whole file — which has happened here once.
HACS_MANIFEST_KEYS = frozenset(
    {
        "content_in_root",
        "country",
        "filename",
        "hacs",
        "hide_default_branch",
        "homeassistant",
        "name",
        "persistent_directory",
        "render_readme",
        "zip_release",
    }
)


@pytest.fixture
def pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


@pytest.fixture
def manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture
def hacs_json() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(HACS_JSON.read_text(encoding="utf-8"))
    return loaded


def test_the_integration_version_matches_the_library_version(pyproject, manifest):
    """PP-SEC-004: HACS shows the manifest version; a mismatch misreports what is installed."""
    assert manifest["version"] == pyproject["project"]["version"]


def test_the_integration_pins_the_library_version_it_was_built_against(pyproject, manifest):
    """PP-SEC-004."""
    expected = f"{DISTRIBUTION}=={pyproject['project']['version']}"

    assert expected in manifest["requirements"]


def test_the_library_is_pinned_exactly_rather_than_by_range(manifest):
    """PP-SEC-005: A range would let Home Assistant install a version never tested here."""
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


def test_hacs_json_carries_only_keys_hacs_accepts(hacs_json):
    """PP-SEC-009: one unknown key invalidates the file, it is not ignored."""
    unknown = sorted(set(hacs_json) - HACS_MANIFEST_KEYS)

    assert not unknown, f"hacs.json carries keys HACS rejects: {', '.join(unknown)}"


def test_hacs_json_names_the_integration_because_hacs_requires_it(hacs_json):
    """PP-SEC-009."""
    assert hacs_json.get("name")


def test_hacs_json_declares_the_country_the_portal_serves(hacs_json):
    """PP-SEC-010: PlusPortal is sold to German utilities only."""
    assert hacs_json.get("country") == "DE"


def test_translations_cover_every_key_the_ui_declares():
    """PP-SEC-006: A missing key shows a raw identifier in the Home Assistant interface."""
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


def test_german_is_actually_translated_not_copied():
    """PP-SEC-006: identical key sets prove nothing about the text itself.

    A translation file copied from English passes a key-parity check while
    leaving the interface in the wrong language.
    """
    base = MANIFEST.parent / "translations"
    english = _leaves(json.loads((base / "en.json").read_text(encoding="utf-8")))
    german = _leaves(json.loads((base / "de.json").read_text(encoding="utf-8")))

    untranslated = sorted(key for key, value in english.items() if german.get(key) == value)

    assert not untranslated, "these read identically in both languages:\n  " + "\n  ".join(
        untranslated
    )


def _leaves(node: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a translation file into dotted key to text."""
    found: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}"
        if isinstance(value, dict):
            found.update(_leaves(value, path))
        else:
            found[path] = value
    return found
