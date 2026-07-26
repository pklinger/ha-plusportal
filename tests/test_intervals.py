"""Quarter-hourly readings — the portal's `power` series turned into energy.

The portal reports this series as average power in kW and labels every point
with the *end* of its interval. Both facts are load-bearing: reading the label
as a start time shifts the whole profile by 15 minutes, and forgetting the
kW->kWh conversion inflates consumption fourfold.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from pyplusportal.client import PlusPortalClient
from pyplusportal.const import PORTAL_TZ
from pyplusportal.models import Channel, Reading, Resolution, ValueState

BASE = "https://123456.plusportal.de"
LOGIN = f"{BASE}/msw/api/auth"
SESSION = f"{BASE}/msw/api/public/session"
DIAGRAM_RESULT = respx.patterns.M(
    url__startswith=f"{BASE}/msw/api/edv/getDiagramResultList/gwa/55789/"
)

CHANNEL = Channel(
    meter_point_id=1000,
    taf_number=55789,
    obis="1-0:1.8.0",
    label="Stromverbrauch",
    unit="kWh",
    periods=("DAY",),
    available_from=datetime(2026, 6, 18, tzinfo=PORTAL_TZ),
    available_to=datetime(2026, 7, 25, tzinfo=PORTAL_TZ),
)


@pytest.fixture
def client():
    return PlusPortalClient(BASE, "user", "pw", retry_backoff=0.0)


@pytest.fixture(autouse=True)
def _authenticated(session_payload):
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    session_payload["loginValidFrom"] = now_ms
    session_payload["loginValidTo"] = now_ms + 3_600_000
    with respx.mock:
        respx.post(LOGIN).mock(return_value=httpx.Response(200))
        respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
        yield


def power_payload(points: list[tuple[datetime, str, str]]) -> httpx.Response:
    """Build a `power` response; each moment is the END of its interval."""
    return httpx.Response(
        200,
        json=[
            {
                "currentDiagramType": "power",
                "data": [
                    {
                        "bez": "Stromverbrauch",
                        "obis": "1-0:1.8.0",
                        "unit": "kW",
                        "values": [
                            {
                                "date": int(moment.timestamp() * 1000),
                                "value": float(kw),
                                "unitA": "kW",
                                "state": flag,
                            }
                            for moment, kw, flag in points
                        ],
                    }
                ],
                "statistic": [],
            }
        ],
    )


def quarter_hours(day: date, count: int, kw: str = "0.2") -> list[tuple[datetime, str, str]]:
    """Interval-END timestamps starting at 00:15 on `day`."""
    midnight = datetime.combine(day, datetime.min.time(), tzinfo=PORTAL_TZ)
    return [(midnight + timedelta(minutes=15 * (i + 1)), kw, "W") for i in range(count)]


# ---------------------------------------------------------------- parsing


def test_an_interval_timestamp_is_the_end_so_the_start_is_shifted_back():
    end = datetime(2026, 7, 20, 0, 15, tzinfo=PORTAL_TZ)
    raw = {"date": int(end.timestamp() * 1000), "value": Decimal("0.2"), "state": "W"}

    reading = Reading.from_interval_api(raw, obis="1-0:1.8.0", interval=timedelta(minutes=15))

    assert reading.start == datetime(2026, 7, 20, 0, 0, tzinfo=PORTAL_TZ)
    assert reading.end == end
    assert reading.duration == timedelta(minutes=15)


def test_average_power_is_converted_to_energy_over_the_interval():
    """2 kW held for a quarter of an hour is 0.5 kWh."""
    end = datetime(2026, 7, 20, 0, 15, tzinfo=PORTAL_TZ)
    raw = {"date": int(end.timestamp() * 1000), "value": Decimal("2"), "state": "W"}

    reading = Reading.from_interval_api(raw, obis="1-0:1.8.0", interval=timedelta(minutes=15))

    assert reading.value == Decimal("0.5")
    assert reading.unit == "kWh"


def test_the_conversion_stays_exact_rather_than_rounding():
    end = datetime(2026, 7, 20, 0, 15, tzinfo=PORTAL_TZ)
    raw = {"date": int(end.timestamp() * 1000), "value": Decimal("0.013"), "state": "W"}

    assert Reading.from_interval_api(
        raw, obis="1-0:1.8.0", interval=timedelta(minutes=15)
    ).value == Decimal("0.00325")


def test_interval_readings_keep_their_own_quality_flag():
    end = datetime(2026, 7, 20, 0, 15, tzinfo=PORTAL_TZ)
    raw = {"date": int(end.timestamp() * 1000), "value": Decimal("1"), "state": "V"}

    reading = Reading.from_interval_api(raw, obis="1-0:1.8.0", interval=timedelta(minutes=15))

    assert reading.state is ValueState.PRELIMINARY
    assert not reading.billable


def test_daily_readings_are_labelled_by_their_start_and_last_a_day():
    raw = {"date": 1782856800000, "value": Decimal("0.0312"), "unitA": "kWh", "state": "W"}

    reading = Reading.from_daily_api(raw, obis="1-0:1.8.0")

    assert reading.start.isoformat() == "2026-07-01T00:00:00+02:00"
    assert reading.resolution is Resolution.DAY


# ------------------------------------------------------------- retrieval


@respx.mock(assert_all_called=False)
async def test_a_full_day_yields_96_intervals_starting_at_midnight(respx_mock, client):
    respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=power_payload(quarter_hours(date(2026, 7, 20), 96))
    )

    async with client:
        readings = await client.get_interval_readings(CHANNEL, date(2026, 7, 20), date(2026, 7, 20))

    assert len(readings) == 96
    assert readings[0].start == datetime(2026, 7, 20, 0, 0, tzinfo=PORTAL_TZ)
    assert readings[-1].end == datetime(2026, 7, 21, 0, 0, tzinfo=PORTAL_TZ)


@respx.mock(assert_all_called=False)
async def test_interval_energy_adds_up_to_the_daily_figure(respx_mock, client):
    """0.0351 kWh over the day, as the daily series reports for 2026-07-20."""
    respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=power_payload(quarter_hours(date(2026, 7, 20), 96, kw="0.0014625"))
    )

    async with client:
        readings = await client.get_interval_readings(CHANNEL, date(2026, 7, 20), date(2026, 7, 20))

    assert sum(r.value for r in readings) == Decimal("0.0351")


@respx.mock(assert_all_called=False)
async def test_the_request_asks_for_the_power_series(respx_mock, client):
    route = respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=power_payload(quarter_hours(date(2026, 7, 20), 4))
    )

    async with client:
        await client.get_interval_readings(CHANNEL, date(2026, 7, 20), date(2026, 7, 20))

    query = parse_qs(urlparse(str(route.calls.last.request.url)).query)
    assert query["diagramType"] == ["power"]
    assert query["period"] == ["month"]


@respx.mock(assert_all_called=False)
async def test_intervals_outside_the_requested_range_are_dropped(respx_mock, client):
    """A value ending at 00:00 belongs to the previous day, not the requested one."""
    points = [
        (datetime(2026, 7, 20, 0, 0, tzinfo=PORTAL_TZ), "1", "W"),  # 19th, 23:45-00:00
        *quarter_hours(date(2026, 7, 20), 96),
        (datetime(2026, 7, 21, 0, 15, tzinfo=PORTAL_TZ), "1", "W"),  # 21st, 00:00-00:15
    ]
    respx_mock.route(DIAGRAM_RESULT).mock(return_value=power_payload(points))

    async with client:
        readings = await client.get_interval_readings(CHANNEL, date(2026, 7, 20), date(2026, 7, 20))

    assert len(readings) == 96
    assert all(r.start.date() == date(2026, 7, 20) for r in readings)


@respx.mock(assert_all_called=False)
async def test_the_interval_length_is_derived_from_the_data(respx_mock, client):
    """Half-hourly meters exist; the spacing must not be hard-coded to 15 minutes."""
    midnight = datetime(2026, 7, 20, tzinfo=PORTAL_TZ)
    points = [(midnight + timedelta(minutes=30 * (i + 1)), "2", "W") for i in range(48)]
    respx_mock.route(DIAGRAM_RESULT).mock(return_value=power_payload(points))

    async with client:
        readings = await client.get_interval_readings(CHANNEL, date(2026, 7, 20), date(2026, 7, 20))

    assert readings[0].duration == timedelta(minutes=30)
    assert readings[0].value == Decimal("1"), "2 kW for half an hour is 1 kWh"


@respx.mock(assert_all_called=False)
async def test_a_single_interval_falls_back_to_the_standard_quarter_hour(respx_mock, client):
    respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=power_payload(quarter_hours(date(2026, 7, 20), 1))
    )

    async with client:
        readings = await client.get_interval_readings(CHANNEL, date(2026, 7, 20), date(2026, 7, 20))

    assert readings[0].duration == timedelta(minutes=15)


@respx.mock(assert_all_called=False)
async def test_an_empty_series_is_not_an_error(respx_mock, client):
    respx_mock.route(DIAGRAM_RESULT).mock(return_value=power_payload([]))

    async with client:
        readings = await client.get_interval_readings(CHANNEL, date(2026, 7, 20), date(2026, 7, 20))

    assert readings == []
