"""Feed metered consumption into Home Assistant's long-term statistics.

Portal values arrive backdated by a day or more. A normal
``state_class: total_increasing`` sensor would book all of it at the moment of
import, so the Energy dashboard would show a spike whenever Home Assistant
happened to poll. External statistics let each value keep the timestamp it was
actually measured at.

Statistics buckets are hourly, so the quarter-hourly readings are summed per
hour on the way in.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

# Home Assistant core imports it from here too; it is simply absent from
# the package's __all__, which mypy's strict re-export rule objects to.
from homeassistant.components.recorder import get_instance  # type: ignore[attr-defined]
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from pyplusportal.cost import Tariff
from pyplusportal.models import Reading

from .const import DOMAIN, STATISTIC_COST, STATISTIC_ENERGY

if TYPE_CHECKING:
    from .coordinator import MeterData, PlusPortalConfigEntry

_LOGGER = logging.getLogger(__name__)

CURRENCY_EUR = "EUR"

#: How far back to look for the statistic preceding a re-imported window.
#: Generous enough to step over a gap in the metering data without an
#: unbounded query.
LOOKBACK = timedelta(days=90)


def _recorder_available(hass: HomeAssistant) -> bool:
    """Whether the recorder is running and can accept statistics."""
    return "recorder" in hass.config.components


#: OBIS codes that carry a readable meaning. Anything else falls back to the
#: sanitised code, which is ugly but unambiguous.
CHANNEL_SLUGS: dict[str, str] = {
    "1-0:1.8.0": "import",
    "1-0:2.8.0": "export",
}


def channel_slug(obis: str) -> str:
    """Turn an OBIS code into an id fragment the recorder accepts."""
    if (known := CHANNEL_SLUGS.get(obis)) is not None:
        return known
    return re.sub(r"[^a-z0-9]+", "_", obis.lower()).strip("_")


def statistic_id(account: str | None, meter_point_id: int, channel: str, kind: str) -> str:
    """Build the external statistic id for one metering point and series.

    The account and the channel are both part of the id. A meter point id is
    only unique inside one portal account, and a meter point can carry several
    channels — import and export — which must never share a series.

    The recorder only accepts lowercase alphanumerics and single underscores,
    so the account's separators are normalised.
    """
    scope = re.sub(r"[^a-z0-9]+", "_", (account or "unknown").lower()).strip("_")
    return f"{DOMAIN}:{scope}_{meter_point_id}_{channel}_{kind}"


def hourly_totals(readings: Iterable[Reading]) -> dict[datetime, Decimal]:
    """Sum readings into hourly buckets keyed by the bucket's start.

    Only billable values are included: provisional readings are replaced by
    real ones later, and importing them would put consumption into the Energy
    dashboard that the supplier never invoices.
    """
    buckets: dict[datetime, Decimal] = defaultdict(Decimal)
    for reading in readings:
        if not reading.billable:
            continue
        bucket = reading.start.replace(minute=0, second=0, microsecond=0)
        buckets[bucket] += reading.value
    return dict(buckets)


async def async_publish_statistics(
    hass: HomeAssistant,
    entry: PlusPortalConfigEntry,
    data: Mapping[int, MeterData],
    tariff: Tariff | None,
) -> None:
    """Write energy — and, when priced, cost — statistics for every meter."""
    if not _recorder_available(hass):
        # The recorder can be disabled in Home Assistant. Consumption sensors
        # still work; only the Energy dashboard history is unavailable.
        _LOGGER.debug("Recorder is not set up, skipping the statistics import")
        return

    for meter_data in data.values():
        meter = meter_data.meter_point
        name = meter.name or str(meter.id)

        for obis, readings in meter_data.channel_readings.items():
            totals = hourly_totals(readings)
            if not totals:
                continue

            channel = channel_slug(obis)
            # The Energy dashboard's own terms, so a series is findable by what
            # the user is looking for rather than by meter number.
            label = "grid consumption" if channel == "import" else f"grid {channel}"

            await _async_publish_series(
                hass,
                statistic_id(entry.unique_id, meter.id, channel, STATISTIC_ENERGY),
                f"{name} {label}",
                UnitOfEnergy.KILO_WATT_HOUR,
                "energy",
                totals,
            )

            # Only what is drawn from the grid is billed at the energy price;
            # pricing an export series at the same rate would be nonsense.
            if tariff is not None and channel == "import":
                price = tariff.energy_price_eur_per_kwh
                await _async_publish_series(
                    hass,
                    statistic_id(entry.unique_id, meter.id, channel, STATISTIC_COST),
                    f"{name} grid cost",
                    CURRENCY_EUR,
                    None,
                    {bucket: value * price for bucket, value in totals.items()},
                )


async def _async_publish_series(
    hass: HomeAssistant,
    stat_id: str,
    name: str,
    unit: str,
    unit_class: str | None,
    totals: Mapping[datetime, Decimal],
) -> None:
    """Import one statistic series, continuing its running sum."""
    first_bucket = min(totals)
    running = await _async_sum_before(hass, stat_id, first_bucket)

    statistics: list[StatisticData] = []
    for bucket in sorted(totals):
        running += totals[bucket]
        statistics.append(
            StatisticData(start=bucket, state=float(totals[bucket]), sum=float(running))
        )

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )
    async_add_external_statistics(hass, metadata, statistics)
    _LOGGER.debug("Imported %d statistics for %s", len(statistics), stat_id)


async def _async_sum_before(hass: HomeAssistant, stat_id: str, boundary: datetime) -> Decimal:
    """Return the running total of the last statistic before ``boundary``.

    The rolling correction window overlaps statistics that already exist. Home
    Assistant replaces rows sharing a start time, so the re-imported window has
    to continue from the sum immediately *preceding* it — not from the newest
    row overall, which lies inside the window and would be double-counted, and
    not from zero, which would discard the whole history before it.
    """
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        boundary - LOOKBACK,
        boundary,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    preceding = [row for row in rows.get(stat_id, []) if row["start"] < boundary.timestamp()]
    if not preceding:
        return Decimal(0)
    return Decimal(str(preceding[-1].get("sum") or 0))
