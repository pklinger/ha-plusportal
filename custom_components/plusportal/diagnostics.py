"""Diagnostics for the PlusPortal integration.

Users attach this output to bug reports, so it must never carry the password,
and it should not expose the meter number either — that identifies a physical
connection point and its address.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_TENANT
from .coordinator import MeterData, PlusPortalConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME}


def _cost(meter_data: MeterData) -> dict[str, Any] | None:
    """Report the cost breakdown, or ``None`` when no tariff is configured.

    A bug report about a wrong bill is unanswerable without these: the split
    between energy and standing charge, what the year is projected at, and how
    much of it is actually backed by data. Amounts are strings so the exact
    Decimal survives the trip through JSON.
    """
    projection = meter_data.projection
    if projection is None:
        return None

    start, end = projection.billing_year
    observed = projection.observed
    return {
        "billing_year": f"{start.isoformat()}..{end.isoformat()}",
        "coverage_percent": str(round(projection.coverage * 100, 2)),
        "billable_kwh": str(observed.energy_kwh),
        "not_billable_kwh": str(observed.excluded_kwh),
        "energy_eur": str(observed.energy_eur),
        "standing_charge_eur": str(observed.base_eur),
        "total_eur": str(observed.total_eur),
        "projected_kwh": str(round(projection.projected_kwh, 3)),
        "projected_eur": str(projection.projected_eur),
        "advances_paid_eur": _optional(projection.advances_paid_eur),
        "advances_due_eur": _optional(projection.advances_due_eur),
        "settlement_eur": _optional(projection.settlement_eur),
    }


def _optional(value: object) -> str | None:
    """Keep an absent amount distinct from zero."""
    return None if value is None else str(value)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PlusPortalConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    return {
        "entry": {
            # Not secret: it identifies the utility, not the customer.
            "tenant": entry.data.get(CONF_TENANT),
            "options": dict(entry.options),
        },
        "tariff_configured": coordinator.tariff is not None,
        "last_update_success": coordinator.last_update_success,
        "meters": [
            {
                "id": meter_data.meter_point.id,
                "category": meter_data.meter_point.category,
                "source_type": meter_data.meter_point.source_type,
                "device_type": meter_data.meter_point.device_type,
                "tafs": [
                    {"number": taf.number, "type": taf.type, "active": taf.active}
                    for taf in meter_data.meter_point.tafs
                ],
                "channels": [
                    {"obis": channel.obis, "unit": channel.unit, "periods": channel.periods}
                    for channel in meter_data.channels
                ],
                "readings": {
                    "count": len(meter_data.readings),
                    "billable": sum(1 for r in meter_data.readings if r.billable),
                    "first": (
                        meter_data.readings[0].start.isoformat() if meter_data.readings else None
                    ),
                    "last": (
                        meter_data.readings[-1].start.isoformat() if meter_data.readings else None
                    ),
                    "interval_seconds": (
                        meter_data.readings[0].duration.total_seconds()
                        if meter_data.readings
                        else None
                    ),
                },
                "cost": _cost(meter_data),
                "overview": [
                    {
                        "obis": overview.obis,
                        "unit": overview.unit,
                        "this_month_sum": str(overview.this_month_sum),
                        "prev_month_sum": str(overview.prev_month_sum),
                        "first_value_at": overview.first_value_at.isoformat(),
                        "last_value_at": overview.last_value_at.isoformat(),
                    }
                    for overview in meter_data.overviews
                ],
            }
            for meter_data in coordinator.data.values()
        ],
        "redacted": async_redact_data(dict(entry.data), TO_REDACT),
    }
