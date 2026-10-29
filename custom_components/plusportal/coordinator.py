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
from pyplusportal.models import (
    Channel,
    MeterPoint,
    Overview,
    Reading,
    billed_obis,
    feed_in_obis,
)

from .const import (
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
    def billed_obis(self) -> str | None:
        """OBIS code of the channel every consumption and cost figure is read from.

        An unfamiliar meter falls back to the first channel the portal offered
        that is not a feed-in register — reporting something is better than
        reporting nothing, but reporting feed-in as consumption is not.
        """
        return billed_obis(self.channel_readings)

    @property
    def feed_in_obis(self) -> str | None:
        """OBIS code of the feed-in channel, or ``None`` if the meter has none."""
        return feed_in_obis(self.channel_readings)

    @property
    def has_feed_in(self) -> bool:
        """Whether this metering point reports energy fed back into the grid."""
        return self.feed_in_obis is not None

    @property
    def readings(self) -> list[Reading]:
        """Readings of the billed channel, which is what the sensors report.

        Consumption, data quality and every cost figure describe what was drawn
        from the grid. An export channel belongs in its own entities and its own
        statistic series, not added into these.
        """
        obis = self.billed_obis
        return self.channel_readings.get(obis, []) if obis is not None else []

    @property
    def feed_in_readings(self) -> list[Reading]:
        """Readings of the feed-in channel; empty for a meter that has none.

        No fallback to another channel: there is nothing worse to show for
        feed-in than consumption under a feed-in name.
        """
        obis = self.feed_in_obis
        return self.channel_readings.get(obis, []) if obis is not None else []

    def overview_for(self, obis: str | None) -> Overview | None:
        """Return the portal's dashboard figures for one OBIS channel."""
        if obis is None:
            return None
        return next((o for o in self.overviews if o.obis == obis), None)

    @property
    def overview(self) -> Overview | None:
        """The billed channel's dashboard figures, which the sensors report.

        PP-HA-030: matched by OBIS rather than taken by position. ``getOverview``
        returns one entry per channel in the portal's own order, so on a meter
        with feed-in the consumption sensors reported the export figures —
        silently, because both are plausible numbers of kilowatt hours.
        """
        if (found := self.overview_for(self.billed_obis)) is not None:
            return found
        # A meter with no readable channel at all — no active tariff use case,
        # say. Anything but a feed-in register is still better than nothing.
        return self.overview_for(billed_obis(o.obis for o in self.overviews))

    @property
    def feed_in_overview(self) -> Overview | None:
        """The feed-in channel's dashboard figures, or ``None`` without one."""
        return self.overview_for(self.feed_in_obis)


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
        self._channels: dict[tuple[str | None, int], list[Channel]] = {}
        self._backfilled: set[tuple[int, str]] = set()

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
            # PP-HA-033: a login with several customer numbers reports the same
            # metering point id once per number, each time with a different
            # direction. One physical meter, so one entry, with the channels of
            # all of them merged into it.
            entry = data.get(meter_point.id)
            if entry is None:
                entry = MeterData(
                    meter_point=meter_point,
                    overviews=[o for o in overviews if o.meter_point_id == meter_point.id],
                )
                data[meter_point.id] = entry

            key = (meter_point.customer, meter_point.id)
            channels = self._channels.get(key)
            if channels is None:
                # Channel discovery costs a request and never changes between
                # polls, so it is done once per Home Assistant run. Keyed by
                # customer number as well: each has its own tariff use cases,
                # and a cache keyed on the meter alone would answer the second
                # customer with the first one's channels.
                channels = await self._client.get_channels(meter_point)
                self._channels[key] = channels

            entry.channels.extend(channels)
            for channel in channels:
                entry.channel_readings[channel.obis] = await self._client.get_interval_readings(
                    channel, self._window_start(entry, channel, today), today
                )

        if (tariff := self.tariff) is not None:
            for entry in data.values():
                entry.projection = project_billing_year(entry.readings, tariff, today=today)

        await async_publish_statistics(self.hass, self.config_entry, data, self.tariff)
        return data

    def _window_start(self, entry: MeterData, channel: Channel, today: date) -> date:
        """First day to re-read for one channel of one metering point.

        The first refresh after a restart pulls the whole history so nothing is
        missing from the statistics. Afterwards there is no point re-importing
        years of unchanged data, so only the window in which values can still
        be corrected is re-read.

        PP-HA-032: the history is a property of the channel, not of the meter.
        Feed-in is usually registered years after the meter was installed, so
        one window for both either truncates the consumption backfill to the day
        the photovoltaic system went live, or asks the portal for months it has
        no feed-in data for on every restart.

        Tracked explicitly rather than by inspecting ``self.data``, which is
        only ``None`` before the first refresh as an implementation detail of
        the coordinator base class.
        """
        overview = entry.overview_for(channel.obis)
        if overview is not None:
            earliest = overview.first_value_at.date()
        else:
            # No dashboard tile for this channel: the diagram configuration's
            # own range is the next best statement of where its data begins.
            earliest = min(channel.available_from.date(), today)

        key = (entry.meter_point.id, channel.obis)
        if key not in self._backfilled:
            self._backfilled.add(key)
            return earliest
        return max(earliest, today - CORRECTION_WINDOW)
