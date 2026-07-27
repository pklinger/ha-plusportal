"""Polling coordinator for one PlusPortal account."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from pyplusportal.client import PlusPortalClient
from pyplusportal.cost import Projection, Tariff, project_billing_year
from pyplusportal.exceptions import AuthenticationError, PlusPortalError
from pyplusportal.models import Channel, MeterPoint, Overview, Reading

from .const import (
    BILLED_OBIS,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TENANT,
    CORRECTION_WINDOW,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    PORTAL_TIMEOUT,
)
from .statistics import async_publish_statistics
from .tariff import tariff_from_options

_LOGGER = logging.getLogger(__name__)

type PlusPortalConfigEntry = ConfigEntry[PlusPortalCoordinator]


@dataclass(slots=True)
class MeterData:
    """Everything the entities need about one metering point."""

    meter_point: MeterPoint
    channels: list[Channel] = field(default_factory=list)
    overviews: list[Overview] = field(default_factory=list)
    channel_readings: dict[str, list[Reading]] = field(default_factory=dict)
    """Readings per OBIS channel. Kept apart because import and export must
    never be summed, and only import is billed."""

    projection: Projection | None = None
    """Cost projection, or ``None`` while no tariff is configured."""

    @property
    def readings(self) -> list[Reading]:
        """Readings of the billed channel, which is what the sensors report.

        Consumption, data quality and every cost figure describe what was drawn
        from the grid. An export channel belongs in its own statistic series, not
        added into these.
        """
        for obis in BILLED_OBIS:
            if obis in self.channel_readings:
                return self.channel_readings[obis]
        # An unfamiliar meter: fall back to whatever the portal offered first
        # rather than reporting nothing at all.
        return next(iter(self.channel_readings.values()), [])

    @property
    def overview(self) -> Overview | None:
        """The first overview channel, which is what the portal's tile shows."""
        return self.overviews[0] if self.overviews else None


class PlusPortalCoordinator(DataUpdateCoordinator[dict[int, MeterData]]):
    """Fetch consumption for every metering point on the account.

    Metered values arrive provisional and are corrected days later, so each
    refresh re-reads a rolling window rather than only what is new. Home
    Assistant overwrites statistics that share a start time, which makes the
    repeated import idempotent and lets corrections land.
    """

    config_entry: PlusPortalConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PlusPortalConfigEntry) -> None:
        """Set up the coordinator for one account."""
        interval = int(entry.options.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(hours=interval),
        )
        # Built by Home Assistant so the SSL context comes from its cache
        # instead of being loaded from disk inside the event loop.
        #
        # Not the *shared* client: this one carries the portal's session cookie,
        # and it needs a timeout of its own. Home Assistant sets none, which
        # leaves httpx's default of five seconds — less than a month of
        # quarter-hourly data takes to arrive, so the backfill would never end.
        self._httpx = create_async_httpx_client(hass, timeout=PORTAL_TIMEOUT)
        self._client = PlusPortalClient(
            entry.data[CONF_TENANT],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            # Built by Home Assistant so the SSL context comes from its cache
            # instead of being loaded from disk inside the event loop.
            #
            # Not the *shared* client: this one carries the portal's session
            # cookie, and it needs a timeout of its own. Home Assistant sets
            # none, which leaves httpx's default of five seconds — less than a
            # month of quarter-hourly data takes to arrive, so the initial
            # backfill would never finish.
            client=self._httpx,
        )
        self._channels: dict[int, list[Channel]] = {}
        self._backfilled: set[int] = set()

    @property
    def tariff(self) -> Tariff | None:
        """The configured tariff, or ``None`` while no prices are set."""
        return tariff_from_options(self.config_entry.options)

    async def async_shutdown_client(self) -> None:
        """Release anything the client owns when the entry is unloaded.

        The httpx client is not closed here. Home Assistant created it and
        closes it on shutdown; an integration doing so itself is flagged as a
        bug. The library only closes clients it created, so it leaves this one
        alone too.
        """
        await self._client.aclose()

    async def _async_update_data(self) -> dict[int, MeterData]:
        """Fetch overviews and the rolling window of interval readings."""
        try:
            return await self._fetch()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PlusPortalError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> dict[int, MeterData]:
        """Do the actual fetching, letting library errors propagate."""
        today = dt_util.now().date()
        overviews = await self._client.get_overview()

        data: dict[int, MeterData] = {}
        for meter_point in await self._client.get_meter_points():
            channels = self._channels.get(meter_point.id)
            if channels is None:
                # Channel discovery costs a request and never changes between
                # polls, so it is done once per Home Assistant run.
                channels = await self._client.get_channels(meter_point)
                self._channels[meter_point.id] = channels

            entry = MeterData(
                meter_point=meter_point,
                channels=channels,
                overviews=[o for o in overviews if o.meter_point_id == meter_point.id],
            )
            for channel in channels:
                entry.channel_readings[channel.obis] = await self._client.get_interval_readings(
                    channel, self._window_start(entry, today), today
                )
            if (tariff := self.tariff) is not None:
                entry.projection = project_billing_year(entry.readings, tariff, today=today)
            data[meter_point.id] = entry

        await async_publish_statistics(self.hass, self.config_entry, data, self.tariff)
        return data

    def _window_start(self, entry: MeterData, today: date) -> date:
        """First day to re-read for one metering point.

        The first refresh after a restart pulls the whole history so nothing is
        missing from the statistics. Afterwards there is no point re-importing
        years of unchanged data, so only the window in which values can still
        be corrected is re-read.

        Tracked explicitly rather than by inspecting ``self.data``, which is
        only ``None`` before the first refresh as an implementation detail of
        the coordinator base class.
        """
        earliest = entry.overview.first_value_at.date() if entry.overview else today
        if entry.meter_point.id not in self._backfilled:
            self._backfilled.add(entry.meter_point.id)
            return earliest
        return max(earliest, today - CORRECTION_WINDOW)
