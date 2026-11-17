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
    CONF_FEED_IN_PRICE,
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
            # The library models the price in cents; the option is euro.
            energy_price_ct_per_kwh=Decimal(str(energy_price)) * 100,
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


def feed_in_price_from_options(options: Mapping[str, Any]) -> Decimal | None:
    """Read the feed-in price in EUR/kWh from config entry options, or ``None``.

    PP-HA-034: independent of the energy price, so it is not part of the
    tariff. ``None`` means feed-in is not priced at all, which is different
    from a price of zero. A stored value that cannot be meant seriously is
    treated as absent rather than raised, for the same reason as in
    ``tariff_from_options``: compensation disappears until it is corrected,
    and the entry still loads.
    """
    raw = options.get(CONF_FEED_IN_PRICE)
    if raw is None:
        return None

    try:
        # Through its text: the form hands over a float, and Decimal(0.0811)
        # is 0.08110000000000000264… rather than what was typed.
        price = Decimal(str(raw))
    except InvalidOperation:
        return None

    # "nan" and "inf" both parse. Either would price every statistic row as
    # not-a-number or infinite, and comparing NaN raises instead of answering.
    if not price.is_finite() or price < 0:
        return None
    return price
