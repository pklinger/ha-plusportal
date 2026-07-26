"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from pyplusportal.json import json_loads

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture_text(name: str) -> str:
    """Read a recorded portal response verbatim, as the portal would send it."""
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def load_fixture(name: str) -> Any:
    """Load a recorded portal response, decoding numbers as Decimal."""
    return json_loads(load_fixture_text(name))


def json_response(name: str, status_code: int = 200) -> httpx.Response:
    """Serve a fixture as an HTTP response, byte-for-byte as recorded.

    Re-serialising the parsed payload would round-trip the numbers through
    ``float`` and quietly undermine the very precision these tests exist to
    protect, so the recorded text is handed over untouched.
    """
    return httpx.Response(
        status_code,
        text=load_fixture_text(name),
        headers={"content-type": "application/json"},
    )


@pytest.fixture
def user_item_list() -> Any:
    return load_fixture("user_item_list.json")


@pytest.fixture
def overview_payload() -> Any:
    return load_fixture("overview.json")


@pytest.fixture
def diagram_config() -> Any:
    return load_fixture("diagram_config.json")


@pytest.fixture
def diagram_result() -> Any:
    return load_fixture("diagram_result_july2026.json")


@pytest.fixture
def session_payload() -> Any:
    return load_fixture("session.json")
