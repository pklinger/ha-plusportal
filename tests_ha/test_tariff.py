"""Turning stored options into a tariff, including the malformed cases.

These are the paths a hand-edited `.storage` file or a future options schema
change reaches. They are cheap to get wrong and expensive to notice: a tariff
that silently becomes `None` removes every cost sensor with no error anywhere.
"""

from __future__ import annotations

import pytest

from custom_components.plusportal.const import (
    CONF_BASE_PRICE,
    CONF_BILLING_YEAR_START,
    CONF_ENERGY_PRICE,
    CONF_MONTHLY_ADVANCE,
)
from custom_components.plusportal.tariff import parse_billing_year_start, tariff_from_options


@pytest.mark.parametrize(
    "value",
    ["0101", "januar", "", "  ", "13-01", "01-32", "02-29", "1-1-1"],
)
def test_an_unusable_billing_year_start_is_rejected(value: str) -> None:
    """PP-HA-006: the cost model would otherwise raise far from the cause."""
    if value.strip() == "":
        # An empty value is the field being left alone, not an error.
        assert parse_billing_year_start(value) == (1, 1)
        return
    with pytest.raises(ValueError):
        parse_billing_year_start(value)


@pytest.mark.parametrize(
    ("value", "expected"), [("01-01", (1, 1)), ("07-01", (7, 1)), (" 12-31 ", (12, 31))]
)
def test_a_valid_billing_year_start_is_parsed(value: str, expected: tuple[int, int]) -> None:
    """PP-HA-006."""
    assert parse_billing_year_start(value) == expected


def test_no_energy_price_means_no_tariff() -> None:
    """PP-HA-011: an unpriced supply, not a free one."""
    assert tariff_from_options({}) is None
    assert tariff_from_options({CONF_BASE_PRICE: 120.0}) is None


def test_a_tariff_is_built_from_complete_options() -> None:
    """PP-HA-023."""
    tariff = tariff_from_options(
        {
            CONF_ENERGY_PRICE: 34.5,
            CONF_BASE_PRICE: 120.0,
            CONF_MONTHLY_ADVANCE: 50.0,
            CONF_BILLING_YEAR_START: "07-01",
        }
    )

    assert tariff is not None
    assert tariff.billing_year_start == (7, 1)
    assert tariff.monthly_advance_eur is not None


@pytest.mark.parametrize(
    "options",
    [
        {CONF_ENERGY_PRICE: "not a number"},
        {CONF_ENERGY_PRICE: 34.5, CONF_BILLING_YEAR_START: "02-29"},
        {CONF_ENERGY_PRICE: 34.5, CONF_BASE_PRICE: "nonsense"},
        {CONF_ENERGY_PRICE: -5},
    ],
)
def test_unusable_options_yield_no_tariff_rather_than_raising(options: dict) -> None:
    """PP-HA-011: setup must not fail because a stored option is malformed.

    Cost simply disappears until the option is corrected; consumption keeps
    working, which is the more important of the two.
    """
    assert tariff_from_options(options) is None


def test_an_absent_advance_stays_absent() -> None:
    """PP-COST-008: zero would read as "nothing to pay"."""
    tariff = tariff_from_options({CONF_ENERGY_PRICE: 34.5})

    assert tariff is not None
    assert tariff.monthly_advance_eur is None
