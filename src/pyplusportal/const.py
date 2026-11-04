"""Endpoints, defaults and magic values of the PlusPortal backend."""

from __future__ import annotations

from datetime import timedelta
from typing import Final
from zoneinfo import ZoneInfo

#: PlusPortal instances are addressed by a six-digit tenant number.
BASE_URL_TEMPLATE: Final = "https://{tenant}.plusportal.de"

#: The backend lives under ``/msw/api``; ``/api`` returns the SPA's HTML shell.
API_PREFIX: Final = "/msw/api"

PATH_LOGIN: Final = f"{API_PREFIX}/auth"
PATH_LOGOUT: Final = f"{API_PREFIX}/auth/logout"
PATH_SESSION: Final = f"{API_PREFIX}/public/session"
#: The customer numbers (GP Nr. Netz) this login may be switched between.
PATH_CUSTOMERS: Final = f"{API_PREFIX}/auth/getKdForSession"
#: Switches the session to one of them; everything afterwards is theirs.
PATH_SELECT_CUSTOMER: Final = f"{API_PREFIX}/auth/loginWithKdNr2"
PATH_USER_ITEM_LIST: Final = f"{API_PREFIX}/account/getUserItemList"
PATH_OVERVIEW: Final = f"{API_PREFIX}/edv/getOverview"
PATH_DIAGRAM_CONFIG: Final = (
    f"{API_PREFIX}/edv/getDiagramConfigById/{{device_type}}/{{device_id}}/{{group}}"
)
PATH_DIAGRAM_RESULT: Final = (
    f"{API_PREFIX}/edv/getDiagramResultList/{{device_type}}/{{device_id}}/{{end_ms}}"
)

#: All portal timestamps are epoch milliseconds anchored to German local time.
PORTAL_TZ: Final = ZoneInfo("Europe/Berlin")

#: Device type used when *reading* diagram data for smart meter gateway meters.
DEVICE_TYPE_GWA: Final = "gwa"
#: Device type used when *configuring* a diagram for a tariff use case.
DEVICE_TYPE_TAF: Final = "TAF"

#: Query constants the web UI sends along with every diagram request.
DIAGRAM_SUB_TYPE: Final = 100
DIAGRAM_KENZ_GROUP: Final = 110
DIAGRAM_STATISTIC_TYPE: Final = 2

#: Tariff use cases (Tarifanwendungsfälle), most detailed first. TAF-7 records a
#: meter reading series, TAF-1 only a data-minimising daily figure.
TAF_PREFERENCE: Final = (7, 6, 2, 1)

#: Active tariff use cases report this status.
TAF_STATUS_ACTIVE: Final = 1

#: Re-login this long before the portal would consider the session stale.
SESSION_EXPIRY_MARGIN_SECONDS: Final = 300

#: Fallback metering interval when it cannot be inferred from the data.
DEFAULT_METERING_INTERVAL: Final = timedelta(minutes=15)

#: OBIS codes for energy drawn from the grid, most specific first. These are
#: the ones an invoice is based on; a feed-in register is not.
BILLED_OBIS: Final = ("1-0:1.8.0",)

#: OBIS codes for energy fed back into the grid (Netzeinspeisung), most
#: specific first. Metered the same way and reported in the same units, which
#: is exactly why it must never be added to what was drawn.
FEED_IN_OBIS: Final = ("1-0:2.8.0",)

DEFAULT_TIMEOUT: Final = 30.0
