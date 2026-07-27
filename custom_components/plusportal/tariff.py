"""Translate Home Assistant options into the library's tariff model."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pyplusportal.cost import Tariff

from .const import (
    CONF_BASE_PRICE,
    CONF_BILLING_YEAR_START,
    CONF_ENERGY_PRICE,
    CONF_MONTHLY_ADVANCE,
    DEFAULT_BILLING_YEAR_START,
)


def parse_billing_year_start(value: Any) -> tuple[int, int]:
    """Parse an ``MM-DD`` string into a (month, day) pair.

    Raises ``ValueError`` for anything the cost model would not accept, so the
    config flow can reject it while the user is still looking at the form.
    """
    # Stripped before the fallback, so a field containing only spaces behaves
    # like an empty one rather than raising.
    text = str(value or "").strip() or DEFAULT_BILLING_YEAR_START
    month_text, separator, day_text = text.partition("-")
    if not separator:
        raise ValueError(f"{text!r} is not in MM-DD form")

    try:
        month, day = int(month_text), int(day_text)
    except ValueError:
        raise ValueError(f"{text!r} is not in MM-DD form") from None

    # Round-trips through Tariff so this and the cost model can never disagree
    # about which dates are acceptable.
    Tariff(
        energy_price_ct_per_kwh=Decimal(0),
        base_price_eur_per_year=Decimal(0),
        billing_year_start=(month, day),
    )
    return month, day


def tariff_from_options(options: Mapping[str, Any]) -> Tariff | None:
    """Build a tariff from config entry options, or ``None`` if unpriced.

    Consumption tracking works without any prices, so an absent energy price is
    a valid configuration rather than an error.
    """
    energy_price = options.get(CONF_ENERGY_PRICE)
    if energy_price is None:
        return None

    try:
        return Tariff(
            energy_price_ct_per_kwh=Decimal(str(energy_price)),
            base_price_eur_per_year=Decimal(str(options.get(CONF_BASE_PRICE) or 0)),
            monthly_advance_eur=(
                Decimal(str(options[CONF_MONTHLY_ADVANCE]))
                if options.get(CONF_MONTHLY_ADVANCE) is not None
                else None
            ),
            billing_year_start=parse_billing_year_start(options.get(CONF_BILLING_YEAR_START)),
        )
    except (InvalidOperation, ValueError):
        return None
