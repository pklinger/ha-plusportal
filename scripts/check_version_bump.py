"""Check that a pull request touching shipped code also bumps the version.

Home Assistant caches an integration by its manifest version and HACS offers
updates by comparing it, so shipping changed code under an unchanged version
means users keep running the old one with no way to tell. The same applies to
the library: its version is what `manifest.json` pins.

Semantic versioning, and the branch name says which part moves:

    fix/…       patch    a bug fix, no behaviour added or removed
    feat/…      minor    new behaviour, backwards compatible
    feat!/…     major    behaviour removed or changed incompatibly
    chore/…     none     nothing users receive changed
    docs/…      none
    refactor/…  none     unless it changes behaviour, then feat/ or fix/

Below 1.0.0 a major bump moves the minor instead, as semver allows.

Run locally with the base branch to compare against:

    uv run python scripts/check_version_bump.py origin/main
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Directories whose contents reach a user's Home Assistant.
SHIPPED = ("src/pyplusportal/", "custom_components/plusportal/")

#: Branch prefixes that do not require a version change.
NO_BUMP_PREFIXES = ("chore/", "docs/", "test/", "ci/", "build/")


def run(*args: str) -> str:
    """Run a git command and return its output."""
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def version_at(ref: str) -> str | None:
    """Project version at a git ref, or ``None`` if it cannot be read."""
    try:
        content = run("show", f"{ref}:pyproject.toml")
    except subprocess.CalledProcessError:
        return None
    return str(tomllib.loads(content)["project"]["version"])


def current_version() -> str:
    """Project version in the working tree."""
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])


def changed_files(base: str) -> list[str]:
    """Files this branch changes relative to ``base``."""
    merge_base = run("merge-base", base, "HEAD")
    return run("diff", "--name-only", merge_base, "HEAD").splitlines()


def main() -> int:
    """Compare the version against the base branch and report."""
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    branch = run("rev-parse", "--abbrev-ref", "HEAD")

    shipped = [f for f in changed_files(base) if f.startswith(SHIPPED)]
    if not shipped:
        print(f"No shipped code changed; no version bump needed ({branch}).")
        return 0

    if branch.startswith(NO_BUMP_PREFIXES):
        print(
            f"::error::{branch} changes shipped code but its prefix says otherwise.\n"
            "Rename the branch to feat/ or fix/, or move the change out of " + ", ".join(SHIPPED)
        )
        for path in shipped:
            print(f"  {path}")
        return 1

    before, now = version_at(base), current_version()
    if before is None:
        print(f"Cannot read the version at {base}; skipping.")
        return 0

    if before == now:
        print(
            f"::error::Shipped code changed but the version is still {now}.\n"
            "Bump it in pyproject.toml and in custom_components/plusportal/manifest.json "
            "(version and the requirements pin), then re-run. See "
            "scripts/check_version_bump.py for which part to move."
        )
        for path in shipped:
            print(f"  {path}")
        return 1

    print(f"Version moved {before} -> {now} for changes in:")
    for path in shipped:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
