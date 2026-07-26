"""Turn metered consumption into money.

The portal knows what you used but not what you pay for it — no account we have
seen exposes tariff data — so prices come from the user and the arithmetic
happens here. Everything is ``Decimal`` and rounds half-up at the cent, the way
an invoice does; anything else drifts once a year's worth of quarter-hourly
values is summed.

Nothing in this module touches the network, the clock, or Home Assistant.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .const import PORTAL_TZ
from .models import Reading

__all__ = [
    "CostBreakdown",
    "Projection",
    "Tariff",
    "billing_year_bounds",
    "cost_of",
    "project_billing_year",
]

_CENT = Decimal("0.01")
_SECONDS_PER_HOUR = Decimal(3600)

#: Nominal year length used to spread the standing charge over a partial period.
#: Utilities pro-rate the Grundpreis this way; a leap day shifts it by 33 cents
#: on a 120 EUR/a tariff, which the annual invoice settles anyway.
_NOMINAL_YEAR_DAYS = Decimal(365)


def money(value: Decimal) -> Decimal:
    """Round an amount to whole cents, half away from zero."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Tariff:
    """What the supplier charges.

    Prices are gross (brutto), matching what appears on a German invoice.
    """

    energy_price_ct_per_kwh: Decimal
    """Arbeitspreis in cents per kWh."""

    base_price_eur_per_year: Decimal
    """Grundpreis in euro per year."""

    monthly_advance_eur: Decimal | None = None
    """Abschlag paid each month, if any. Without it no settlement is projected."""

    billing_year_start: tuple[int, int] = (1, 1)
    """(month, day) the billing year starts on. Defaults to the calendar year."""

    def __post_init__(self) -> None:
        """Reject prices and anniversaries that cannot be meant seriously."""
        if self.energy_price_ct_per_kwh < 0:
            raise ValueError("the energy price cannot be negative")
        if self.base_price_eur_per_year < 0:
            raise ValueError("the base price cannot be negative")
        if self.monthly_advance_eur is not None and self.monthly_advance_eur < 0:
            raise ValueError("the monthly advance cannot be negative")

        month, day = self.billing_year_start
        if (month, day) == (2, 29):
            raise ValueError(
                "a billing year cannot start on 29 February — it does not exist every year"
            )
        try:
            date(2001, month, day)  # a non-leap year, so 29 February is already out
        except ValueError as err:
            raise ValueError(
                f"billing year start {month:02d}-{day:02d} is not a valid date"
            ) from err

    @property
    def energy_price_eur_per_kwh(self) -> Decimal:
        """Arbeitspreis in euro per kWh."""
        return self.energy_price_ct_per_kwh / Decimal(100)


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """What a set of readings costs, split the way an invoice splits it."""

    energy_kwh: Decimal
    """Billable energy. Preliminary values are excluded."""

    excluded_kwh: Decimal
    """Energy seen but not billable, so the gap is visible rather than silent."""

    covered: timedelta
    """How much time the readings actually account for."""

    energy_eur: Decimal
    base_eur: Decimal

    @property
    def total_eur(self) -> Decimal:
        """Sum of the rounded parts, so the figures shown always add up."""
        return self.energy_eur + self.base_eur


@dataclass(frozen=True, slots=True)
class Projection:
    """An estimate of where the billing year will land."""

    billing_year: tuple[date, date]
    observed: CostBreakdown
    projected_kwh: Decimal
    projected_eur: Decimal

    coverage: Decimal
    """Fraction of the billing year backed by real data, 0 to 1."""

    advances_paid_eur: Decimal | None
    advances_due_eur: Decimal | None

    settlement_eur: Decimal | None
    """Positive means an additional payment is expected, negative a refund."""


def _total_hours(readings: Iterable[Reading]) -> Decimal:
    """Total time covered by the given readings, in hours."""
    seconds = sum((r.duration.total_seconds() for r in readings), 0.0)
    return Decimal(seconds) / _SECONDS_PER_HOUR


def cost_of(readings: Sequence[Reading], tariff: Tariff) -> CostBreakdown:
    """Price a set of readings, charging the standing charge pro rata.

    Only billable readings (`W` and `E`) are charged: preliminary values get
    replaced by real ones later, so billing them invents consumption that the
    supplier never invoices.
    """
    billable = [r for r in readings if r.billable]
    energy_kwh = sum((r.value for r in billable), Decimal(0))
    excluded_kwh = sum((r.value for r in readings if not r.billable), Decimal(0))

    covered_hours = _total_hours(billable)
    base_share = covered_hours / (_NOMINAL_YEAR_DAYS * Decimal(24))

    return CostBreakdown(
        energy_kwh=energy_kwh,
        excluded_kwh=excluded_kwh,
        covered=timedelta(hours=float(covered_hours)),
        energy_eur=money(energy_kwh * tariff.energy_price_eur_per_kwh),
        base_eur=money(tariff.base_price_eur_per_year * base_share),
    )


def billing_year_bounds(reference: date, tariff: Tariff) -> tuple[date, date]:
    """Return the billing year containing ``reference``, as inclusive bounds."""
    month, day = tariff.billing_year_start

    start = date(reference.year, month, day)
    if start > reference:
        start = date(reference.year - 1, month, day)

    end = date(start.year + 1, month, day) - timedelta(days=1)
    return start, end


def _elapsed_months(start: date, today: date) -> int:
    """Count the monthly advances due by ``today``, including the first month."""
    if today < start:
        return 0
    months = (today.year - start.year) * 12 + (today.month - start.month) + 1
    return max(0, min(12, months))


def project_billing_year(readings: Sequence[Reading], tariff: Tariff, *, today: date) -> Projection:
    """Extrapolate the billing year from the data available so far.

    The rate is taken per *covered hour* rather than per calendar day. That way
    a half-finished final day and gaps in the middle both behave correctly: an
    incomplete day does not drag the average down, and a missing week is filled
    at the observed rate instead of counting as zero consumption.
    """
    start, end = billing_year_bounds(today, tariff)
    in_year = [r for r in readings if start <= r.start.date() <= end]
    observed = cost_of(in_year, tariff)

    year_start = datetime.combine(start, time.min, tzinfo=PORTAL_TZ)
    year_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=PORTAL_TZ)
    year_hours = Decimal((year_end - year_start).total_seconds()) / _SECONDS_PER_HOUR
    covered_hours = _total_hours(r for r in in_year if r.billable)

    if covered_hours > 0:
        # Multiply before dividing: it keeps whole-number results exact.
        projected_kwh = observed.energy_kwh * year_hours / covered_hours
        coverage = covered_hours / year_hours
    else:
        projected_kwh = Decimal(0)
        coverage = Decimal(0)

    projected_eur = money(projected_kwh * tariff.energy_price_eur_per_kwh) + money(
        tariff.base_price_eur_per_year
    )

    advances_paid = advances_due = settlement = None
    if tariff.monthly_advance_eur is not None:
        advances_paid = money(tariff.monthly_advance_eur * _elapsed_months(start, today))
        advances_due = money(tariff.monthly_advance_eur * 12)
        settlement = projected_eur - advances_due

    return Projection(
        billing_year=(start, end),
        observed=observed,
        projected_kwh=projected_kwh,
        projected_eur=projected_eur,
        coverage=coverage,
        advances_paid_eur=advances_paid,
        advances_due_eur=advances_due,
        settlement_eur=settlement,
    )
