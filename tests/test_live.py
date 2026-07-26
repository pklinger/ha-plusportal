"""Reconciliation against a real portal.

Excluded from the default run and from CI. Everything identifying — tenant,
username, password — comes from the environment, so no real account detail ever
enters this repository.

    cp .env.example .env    # fill in, it is gitignored
    uv run pytest -m live

The assertions deliberately check *invariants that hold for any account*, never
values from one particular meter: that the three figures the portal reports
agree with each other, that intervals tile a day exactly, and that timestamps
are usable as Home Assistant statistics. A hard-coded kWh figure would only
prove that one household's July was recorded correctly.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

from pyplusportal.client import PlusPortalClient
from pyplusportal.const import PORTAL_TZ

pytestmark = pytest.mark.live

ENV_TENANT = "PLUSPORTAL_TENANT"
ENV_USERNAME = "PLUSPORTAL_USERNAME"
ENV_PASSWORD = "PLUSPORTAL_PASSWORD"


@pytest.fixture(scope="module")
def credentials() -> dict[str, str]:
    """Read credentials from the environment, or skip the whole module."""
    missing = [
        name
        for name in (ENV_TENANT, ENV_USERNAME, ENV_PASSWORD)
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip(f"live tests need {', '.join(missing)}")

    return {
        "tenant": os.environ[ENV_TENANT],
        "username": os.environ[ENV_USERNAME],
        "password": os.environ[ENV_PASSWORD],
    }


@pytest.fixture
async def client(credentials: dict[str, str]):
    """A client bound to the configured account."""
    async with PlusPortalClient(
        credentials["tenant"], credentials["username"], credentials["password"]
    ) as portal:
        yield portal


async def test_the_credentials_are_accepted(client) -> None:
    session = await client.login()

    assert session.user_id
    assert not session.is_expired()


async def test_the_account_has_at_least_one_metering_point(client) -> None:
    meter_points = await client.get_meter_points()

    assert meter_points, "no metering point — the integration would have nothing to show"
    assert any(point.primary_taf for point in meter_points), "no active tariff use case"


async def test_every_metering_point_exposes_a_channel(client) -> None:
    for point in await client.get_meter_points():
        assert await client.get_channels(point), f"meter {point.id} has no channel"


async def test_interval_and_daily_readings_agree_with_the_portals_own_total(
    client,
) -> None:
    """The reconciliation that makes the kW->kWh conversion trustworthy.

    Three independent figures must match: the quarter-hourly series converted to
    energy, the daily series, and the aggregate the portal shows on its tile.
    """
    overviews = {o.meter_point_id: o for o in await client.get_overview()}
    today = date.today()
    first_of_month = today.replace(day=1)
    # The portal publishes yesterday's values, so the current day is incomplete.
    last_complete = today - timedelta(days=1)
    if last_complete < first_of_month:
        pytest.skip("no complete day in the current month yet")

    for point in await client.get_meter_points():
        overview = overviews.get(point.id)
        if overview is None or overview.this_month_sum is None:
            continue

        for channel in await client.get_channels(point):
            intervals = await client.get_interval_readings(
                channel, first_of_month, last_complete
            )
            daily = await client.get_daily_readings(channel, first_of_month, last_complete)
            if not intervals:
                continue

            assert sum(r.value for r in intervals) == sum(r.value for r in daily), (
                "the quarter-hourly series does not add up to the daily series"
            )
            assert sum(r.value for r in daily) == overview.this_month_sum, (
                "the daily series does not match the portal's own month total"
            )


async def test_a_complete_day_is_tiled_exactly_by_its_intervals(client) -> None:
    """No gaps and no overlaps, or the Energy dashboard would misreport."""
    day = date.today() - timedelta(days=2)

    for point in await client.get_meter_points():
        for channel in await client.get_channels(point):
            readings = await client.get_interval_readings(channel, day, day)
            if not readings:
                continue

            assert all(r.start.date() == day for r in readings)
            for earlier, later in zip(readings, readings[1:], strict=False):
                assert earlier.end == later.start, "gap or overlap between intervals"

            covered = sum((r.duration for r in readings), timedelta())
            assert covered == timedelta(days=1), f"{covered} of the day is covered"


async def test_readings_are_usable_as_home_assistant_statistics(client) -> None:
    """Timestamps must be aware and land on a whole minute of a known hour."""
    day = date.today() - timedelta(days=2)

    for point in await client.get_meter_points():
        for channel in await client.get_channels(point):
            for reading in await client.get_interval_readings(channel, day, day):
                assert reading.start.tzinfo is not None
                assert reading.start.utcoffset() is not None
                assert reading.start.second == 0
                assert reading.start.microsecond == 0
                assert reading.value >= 0
                assert isinstance(reading.value, Decimal)


async def test_the_portal_reports_which_values_are_billable(client) -> None:
    """Without the quality flag there is no way to bill accurately."""
    day = date.today() - timedelta(days=2)

    for point in await client.get_meter_points():
        for channel in await client.get_channels(point):
            readings = await client.get_interval_readings(channel, day, day)
            if readings:
                assert all(r.state is not None for r in readings)
                return

    pytest.skip("no readings available to inspect")


async def test_the_timezone_matches_what_the_portal_uses(client) -> None:
    """A mismatch would shift every value by an hour twice a year."""
    day = date.today() - timedelta(days=2)

    for point in await client.get_meter_points():
        for channel in await client.get_channels(point):
            readings = await client.get_interval_readings(channel, day, day)
            if readings:
                assert readings[0].start.tzinfo is PORTAL_TZ
                assert readings[0].start.hour == 0, "a day must start at local midnight"
                return
