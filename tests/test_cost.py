"""Cost calculation and bill projection — the "centgenau" part.

Everything here is pure arithmetic over readings and a tariff: no Home
Assistant, no network, no clock of its own.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from pyplusportal.const import PORTAL_TZ
from pyplusportal.cost import (
    Tariff,
    billing_year_bounds,
    cost_of,
    project_billing_year,
)
from pyplusportal.models import Reading, ValueState

TARIFF = Tariff(
    energy_price_ct_per_kwh=Decimal("34.5"),
    base_price_eur_per_year=Decimal("120"),
    monthly_advance_eur=Decimal("50"),
)


def reading(
    day: date,
    kwh: str,
    *,
    hour: int = 0,
    state: ValueState = ValueState.TRUE_VALUE,
    duration: timedelta = timedelta(minutes=15),
) -> Reading:
    return Reading(
        start=datetime.combine(day, datetime.min.time(), tzinfo=PORTAL_TZ) + timedelta(hours=hour),
        duration=duration,
        value=Decimal(kwh),
        unit="kWh",
        obis="1-0:1.8.0",
        state=state,
    )


def days(start: date, count: int, kwh_per_day: str) -> list[Reading]:
    """One full-day reading per day."""
    return [
        reading(start + timedelta(days=offset), kwh_per_day, duration=timedelta(days=1))
        for offset in range(count)
    ]


# ------------------------------------------------------------------ Tariff


def test_a_tariff_rejects_a_negative_energy_price():
    with pytest.raises(ValueError, match="negative"):
        Tariff(energy_price_ct_per_kwh=Decimal("-1"), base_price_eur_per_year=Decimal("0"))


def test_a_tariff_rejects_a_negative_base_price():
    with pytest.raises(ValueError, match="negative"):
        Tariff(energy_price_ct_per_kwh=Decimal("30"), base_price_eur_per_year=Decimal("-5"))


def test_a_billing_year_cannot_start_on_a_date_that_does_not_exist_every_year():
    with pytest.raises(ValueError, match="29 February"):
        Tariff(
            energy_price_ct_per_kwh=Decimal("30"),
            base_price_eur_per_year=Decimal("0"),
            billing_year_start=(2, 29),
        )


def test_a_tariff_rejects_an_impossible_month():
    with pytest.raises(ValueError, match="billing year"):
        Tariff(
            energy_price_ct_per_kwh=Decimal("30"),
            base_price_eur_per_year=Decimal("0"),
            billing_year_start=(13, 1),
        )


# -------------------------------------------------------------- energy cost


def test_energy_is_charged_at_the_tariff_price():
    """100 kWh at 34.5 ct is 34.50 EUR."""
    breakdown = cost_of(days(date(2026, 1, 1), 100, "1"), TARIFF)

    assert breakdown.energy_kwh == Decimal("100")
    assert breakdown.energy_eur == Decimal("34.50")


def test_only_billable_values_are_charged():
    """Preliminary values get replaced later; charging them invents consumption."""
    readings = [
        reading(date(2026, 1, 1), "10", state=ValueState.TRUE_VALUE),
        reading(date(2026, 1, 2), "10", state=ValueState.SUBSTITUTE),
        reading(date(2026, 1, 3), "10", state=ValueState.PRELIMINARY),
        reading(date(2026, 1, 4), "10", state=ValueState.UNKNOWN),
    ]

    breakdown = cost_of(readings, TARIFF)

    assert breakdown.energy_kwh == Decimal("20")
    assert breakdown.excluded_kwh == Decimal("20")


def test_the_base_price_is_charged_pro_rata_for_the_covered_time():
    """A quarter of a 365-day year at 120 EUR/a is roughly 30 EUR."""
    breakdown = cost_of(days(date(2026, 1, 1), 91, "0"), TARIFF)

    assert breakdown.base_eur == Decimal("29.92")


def test_the_total_is_the_sum_of_the_rounded_parts():
    """An invoice that does not add up is worse than one that rounds a cent."""
    breakdown = cost_of(days(date(2026, 1, 1), 30, "3.333"), TARIFF)

    assert breakdown.energy_kwh == Decimal("99.990")
    assert breakdown.energy_eur == Decimal("34.50")  # 99.99 kWh x 0.345 EUR
    assert breakdown.base_eur == Decimal("9.86")  # 120 EUR x 30/365
    assert breakdown.total_eur == Decimal("44.36")


def test_money_is_rounded_half_up_not_to_even():
    """0.125 EUR must become 0.13, the way invoices round."""
    tariff = Tariff(energy_price_ct_per_kwh=Decimal("25"), base_price_eur_per_year=Decimal("0"))
    breakdown = cost_of([reading(date(2026, 1, 1), "0.5")], tariff)

    assert breakdown.energy_eur == Decimal("0.13")


def test_no_readings_still_costs_nothing_rather_than_failing():
    breakdown = cost_of([], TARIFF)

    assert breakdown.energy_kwh == Decimal(0)
    assert breakdown.total_eur == Decimal("0.00")


def test_quarter_hourly_and_daily_readings_reach_the_same_total():
    daily = days(date(2026, 1, 1), 1, "2.4")
    quarters = [reading(date(2026, 1, 1), "0.025", hour=0) for _ in range(96)]

    assert cost_of(daily, TARIFF).energy_eur == cost_of(quarters, TARIFF).energy_eur


# ------------------------------------------------------------ billing year


def test_the_billing_year_defaults_to_the_calendar_year():
    assert billing_year_bounds(date(2026, 7, 25), TARIFF) == (
        date(2026, 1, 1),
        date(2026, 12, 31),
    )


def test_a_billing_year_starting_mid_year_runs_into_the_next():
    tariff = Tariff(
        energy_price_ct_per_kwh=Decimal("30"),
        base_price_eur_per_year=Decimal("0"),
        billing_year_start=(7, 1),
    )

    assert billing_year_bounds(date(2026, 7, 25), tariff) == (
        date(2026, 7, 1),
        date(2027, 6, 30),
    )


def test_before_the_anniversary_the_previous_billing_year_is_still_running():
    tariff = Tariff(
        energy_price_ct_per_kwh=Decimal("30"),
        base_price_eur_per_year=Decimal("0"),
        billing_year_start=(7, 1),
    )

    assert billing_year_bounds(date(2026, 6, 30), tariff) == (
        date(2025, 7, 1),
        date(2026, 6, 30),
    )


def test_the_first_day_of_a_billing_year_belongs_to_that_year():
    tariff = Tariff(
        energy_price_ct_per_kwh=Decimal("30"),
        base_price_eur_per_year=Decimal("0"),
        billing_year_start=(7, 1),
    )

    assert billing_year_bounds(date(2026, 7, 1), tariff)[0] == date(2026, 7, 1)


def test_a_leap_day_inside_the_billing_year_is_counted():
    assert billing_year_bounds(date(2028, 5, 1), TARIFF) == (
        date(2028, 1, 1),
        date(2028, 12, 31),
    )


# -------------------------------------------------------------- projection


def test_a_projection_extrapolates_the_observed_rate_over_the_whole_year():
    """10 kWh/day for 100 days projects to 3650 kWh across a 365-day year."""
    projection = project_billing_year(
        days(date(2026, 1, 1), 100, "10"), TARIFF, today=date(2026, 4, 11)
    )

    assert projection.observed.energy_kwh == Decimal("1000")
    assert projection.projected_kwh == Decimal("3650")


def test_gaps_in_the_data_are_filled_at_the_observed_rate():
    """Missing days must not read as zero consumption."""
    sparse = days(date(2026, 1, 1), 50, "10") + days(date(2026, 3, 1), 50, "10")

    projection = project_billing_year(sparse, TARIFF, today=date(2026, 4, 30))

    assert projection.observed.energy_kwh == Decimal("1000")
    assert projection.projected_kwh == Decimal("3650")


def test_a_partial_final_day_does_not_drag_the_average_down():
    """Extrapolating per covered hour, not per calendar day, handles this."""
    full = days(date(2026, 1, 1), 10, "24")
    half_a_day = [reading(date(2026, 1, 11), "12", duration=timedelta(hours=12))]

    projection = project_billing_year(full + half_a_day, TARIFF, today=date(2026, 1, 11))

    assert projection.projected_kwh == Decimal("8760"), "24 kWh/day over 365 days"


def test_the_projection_charges_the_full_year_base_price():
    projection = project_billing_year(
        days(date(2026, 1, 1), 10, "0"), TARIFF, today=date(2026, 1, 10)
    )

    assert projection.projected_eur == Decimal("120.00")


def test_coverage_reports_how_much_of_the_year_is_backed_by_data():
    projection = project_billing_year(
        days(date(2026, 1, 1), 73, "1"), TARIFF, today=date(2026, 3, 14)
    )

    assert projection.coverage == Decimal("0.2")


def test_without_any_data_the_projection_is_the_base_price_alone():
    projection = project_billing_year([], TARIFF, today=date(2026, 3, 14))

    assert projection.projected_kwh == Decimal(0)
    assert projection.projected_eur == Decimal("120.00")
    assert projection.coverage == Decimal(0)


def test_readings_outside_the_billing_year_are_ignored():
    inside = days(date(2026, 1, 1), 10, "10")
    before = days(date(2025, 12, 20), 10, "999")

    projection = project_billing_year(before + inside, TARIFF, today=date(2026, 1, 10))

    assert projection.observed.energy_kwh == Decimal("100")


# -------------------------------------------------------------- settlement


def test_a_shortfall_becomes_an_expected_additional_payment():
    """3650 kWh at 34.5 ct plus 120 EUR base is 1379.25; 12x50 paid leaves 779.25."""
    projection = project_billing_year(
        days(date(2026, 1, 1), 100, "10"), TARIFF, today=date(2026, 4, 11)
    )

    assert projection.advances_due_eur == Decimal("600.00")
    assert projection.settlement_eur == Decimal("779.25")


def test_an_overpayment_becomes_a_negative_settlement():
    generous = Tariff(
        energy_price_ct_per_kwh=Decimal("30"),
        base_price_eur_per_year=Decimal("0"),
        monthly_advance_eur=Decimal("100"),
    )

    projection = project_billing_year(
        days(date(2026, 1, 1), 100, "1"), generous, today=date(2026, 4, 11)
    )

    assert projection.settlement_eur is not None
    assert projection.settlement_eur < 0


def test_advances_paid_so_far_counts_elapsed_months():
    projection = project_billing_year(
        days(date(2026, 1, 1), 100, "10"), TARIFF, today=date(2026, 4, 11)
    )

    assert projection.advances_paid_eur == Decimal("200.00"), "Jan-Apr, four payments"


def test_without_a_configured_advance_there_is_no_settlement_figure():
    no_advance = Tariff(
        energy_price_ct_per_kwh=Decimal("34.5"), base_price_eur_per_year=Decimal("120")
    )

    projection = project_billing_year(
        days(date(2026, 1, 1), 100, "10"), no_advance, today=date(2026, 4, 11)
    )

    assert projection.settlement_eur is None
    assert projection.advances_due_eur is None


def test_a_projection_of_a_finished_year_equals_its_actual_cost():
    """With the year fully covered, projection and observation must agree."""
    full_year = days(date(2026, 1, 1), 365, "10")

    projection = project_billing_year(full_year, TARIFF, today=date(2026, 12, 31))

    assert projection.coverage == Decimal(1)
    assert projection.projected_eur == projection.observed.total_eur
