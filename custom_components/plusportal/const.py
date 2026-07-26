"""Constants for the PlusPortal integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "plusportal"

CONF_TENANT: Final = "tenant"
CONF_ENERGY_PRICE: Final = "energy_price_ct_per_kwh"
CONF_BASE_PRICE: Final = "base_price_eur_per_year"
CONF_MONTHLY_ADVANCE: Final = "monthly_advance_eur"
CONF_BILLING_YEAR_START: Final = "billing_year_start"
CONF_SCAN_INTERVAL_HOURS: Final = "scan_interval_hours"

DEFAULT_BILLING_YEAR_START: Final = "01-01"
DEFAULT_SCAN_INTERVAL_HOURS: Final = 6

#: The portal publishes yesterday's values, so polling more often buys nothing
#: and only puts load on someone else's server.
MIN_SCAN_INTERVAL_HOURS: Final = 1
MAX_SCAN_INTERVAL_HOURS: Final = 24

#: Metered values arrive provisional and are corrected later, so every refresh
#: re-fetches this many days back and lets the newer values win.
CORRECTION_WINDOW: Final = timedelta(days=21)

#: Statistic id suffixes, as they appear in the Energy dashboard.
STATISTIC_ENERGY: Final = "energy"
STATISTIC_COST: Final = "cost"
