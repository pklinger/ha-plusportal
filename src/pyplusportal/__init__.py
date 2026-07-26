"""Async client for PlusPortal energy customer portals.

PlusPortal is a white-label customer portal by Thüga SmartService GmbH, run by
German utilities under a per-tenant subdomain such as ``123456.plusportal.de``.
This package reads metering data out of it and knows nothing about Home
Assistant, so it can be used and tested on its own.
"""

from __future__ import annotations

from .exceptions import (
    AuthenticationError,
    ParseError,
    PlusPortalError,
    PortalUnavailableError,
)
from .models import (
    Channel,
    MeterPoint,
    Overview,
    Reading,
    Resolution,
    Session,
    Taf,
    ValueState,
)

__all__ = [
    "AuthenticationError",
    "Channel",
    "MeterPoint",
    "Overview",
    "ParseError",
    "PlusPortalError",
    "PortalUnavailableError",
    "Reading",
    "Resolution",
    "Session",
    "Taf",
    "ValueState",
]
