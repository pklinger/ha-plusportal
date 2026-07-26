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

DEFAULT_TIMEOUT: Final = 30.0
