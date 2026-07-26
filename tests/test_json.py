"""Decimal-preserving JSON round-tripping."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pyplusportal.json import json_dumps, json_loads


def test_fractional_numbers_decode_as_decimal():
    """PP-EXT-009."""
    assert json_loads('{"v": 0.018321}')["v"] == Decimal("0.018321")


def test_whole_numbers_stay_integers():
    value = json_loads('{"v": 1782856800000}')["v"]

    assert value == 1782856800000
    assert isinstance(value, int)


def test_bytes_are_accepted():
    assert json_loads(b'{"v": 1}')["v"] == 1


def test_decimals_serialise_as_bare_json_numbers():
    assert json_dumps({"v": Decimal("0.018321")}) == '{"v": 0.018321}'


def test_trailing_zeros_are_preserved_rather_than_normalised():
    assert json_dumps({"v": Decimal("0.7500")}) == '{"v": 0.7500}'


def test_a_full_round_trip_loses_nothing():
    payload = {"a": Decimal("0.757899"), "b": [Decimal("-1.5"), 3, "0.1"], "c": None}

    assert json_loads(json_dumps(payload)) == payload


def test_strings_that_look_like_the_internal_marker_are_not_corrupted():
    payload = {"note": "@@DECIMAL@@ is just text"}

    assert json_loads(json_dumps(payload)) == payload


def test_indentation_is_supported_for_readable_fixtures():
    assert json_dumps({"v": Decimal("1.5")}, indent=2) == '{\n  "v": 1.5\n}'


def test_non_ascii_survives_unescaped():
    assert json_dumps({"v": "Zählerstandsgang"}) == '{"v": "Zählerstandsgang"}'


def test_unserialisable_objects_are_rejected_loudly():
    with pytest.raises(TypeError):
        json_dumps({"v": object()})
