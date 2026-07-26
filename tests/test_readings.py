"""Retrieval of meter points, channels and daily consumption values."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from pyplusportal.client import PlusPortalClient, _month_windows
from pyplusportal.const import PORTAL_TZ
from pyplusportal.models import Channel, MeterPoint, ValueState

from .conftest import json_response

BASE = "https://123456.plusportal.de"
LOGIN = f"{BASE}/msw/api/auth"
SESSION = f"{BASE}/msw/api/public/session"
USER_ITEMS = f"{BASE}/msw/api/account/getUserItemList"
DIAGRAM_CONFIG = f"{BASE}/msw/api/edv/getDiagramConfigById/TAF/55789/55789"
DIAGRAM_RESULT = respx.patterns.M(
    url__startswith=f"{BASE}/msw/api/edv/getDiagramResultList/gwa/55789/"
)

JULY_CHANNEL = Channel(
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
    """Every test here starts from a working login."""
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    session_payload["loginValidFrom"] = now_ms
    session_payload["loginValidTo"] = now_ms + 3_600_000
    with respx.mock:
        respx.post(LOGIN).mock(return_value=httpx.Response(200))
        respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
        yield


def diagram_payload(points: list[tuple[datetime, str, str]]) -> httpx.Response:
    """Build a getDiagramResultList response out of (moment, value, flag) rows."""
    values = [
        {
            "date": int(moment.timestamp() * 1000),
            "value": float(value),
            "unitA": "kWh",
            "state": flag,
            "bez": "Stromverbrauch",
        }
        for moment, value, flag in points
    ]
    return httpx.Response(
        200,
        json=[
            {
                "currentDiagramPeriod": "MONTH",
                "currentDiagramType": "consumption",
                "data": [
                    {
                        "bez": "Stromverbrauch",
                        "values": values,
                        "unit": "kWh",
                        "obis": "1-0:1.8.0",
                        "visible": True,
                    }
                ],
                "statistic": [],
            }
        ],
    )


def daily(start: date, count: int, value: str = "1.5") -> list[tuple[datetime, str, str]]:
    """Local-midnight rows for `count` consecutive days."""
    return [
        (
            datetime.combine(start + timedelta(days=offset), time.min, tzinfo=PORTAL_TZ),
            value,
            "W",
        )
        for offset in range(count)
    ]


def query_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(request.url)).query)


# ------------------------------------------------------------ meter points


@respx.mock(assert_all_called=False)
async def test_meter_points_are_fetched_and_parsed(respx_mock, client):
    route = respx_mock.get(USER_ITEMS).mock(return_value=json_response("user_item_list.json"))

    async with client:
        points = await client.get_meter_points()

    assert [point.name for point in points] == ["1ABC0000000000*"]
    assert query_of(route.calls.last.request)["page"] == ["0"]


# ---------------------------------------------------------------- channels


@respx.mock(assert_all_called=False)
async def test_channels_are_read_for_the_primary_tariff_use_case(
    respx_mock, client, user_item_list
):
    route = respx_mock.get(DIAGRAM_CONFIG).mock(return_value=json_response("diagram_config.json"))
    meter_point = MeterPoint.list_from_api(user_item_list)[0]

    async with client:
        channels = await client.get_channels(meter_point)

    assert [channel.obis for channel in channels] == ["1-0:1.8.0"]
    assert channels[0].taf_number == 55789
    assert query_of(route.calls.last.request)["diagramSubType"] == ["1000"]


@respx.mock(assert_all_called=False)
async def test_a_meter_point_without_an_active_taf_yields_no_channels(
    respx_mock, client, user_item_list
):
    for taf in user_item_list[0]["userItems"][0]["tafs"]:
        taf["status"] = 0
    config = respx_mock.get(DIAGRAM_CONFIG).mock(return_value=json_response("diagram_config.json"))

    async with client:
        assert await client.get_channels(MeterPoint.list_from_api(user_item_list)[0]) == []

    assert config.call_count == 0, "must not query the portal when there is nothing to query for"


# ------------------------------------------------------------ daily values


@respx.mock(assert_all_called=False)
async def test_july_readings_match_the_portals_own_monthly_total(respx_mock, client):
    """The acceptance criterion: our sum equals getOverview.thisMonthSum exactly."""
    respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=json_response("diagram_result_july2026.json")
    )

    async with client:
        readings = await client.get_daily_readings(
            JULY_CHANNEL, date(2026, 7, 1), date(2026, 7, 24)
        )

    assert len(readings) == 24
    assert sum(r.value for r in readings) == Decimal("0.757899")
    assert readings[0].day == date(2026, 7, 1)
    assert readings[-1].day == date(2026, 7, 24)


@respx.mock(assert_all_called=False)
async def test_readings_keep_the_quality_flags_that_decide_billability(respx_mock, client):
    respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=json_response("diagram_result_july2026.json")
    )

    async with client:
        readings = await client.get_daily_readings(
            JULY_CHANNEL, date(2026, 7, 1), date(2026, 7, 24)
        )

    by_day = {r.day: r for r in readings}
    assert by_day[date(2026, 7, 8)].state is ValueState.SUBSTITUTE
    assert by_day[date(2026, 7, 1)].state is ValueState.TRUE_VALUE
    assert all(r.billable for r in readings)


@respx.mock(assert_all_called=False)
async def test_the_request_asks_for_daily_buckets_of_the_right_channel(respx_mock, client):
    route = respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=json_response("diagram_result_july2026.json")
    )

    async with client:
        await client.get_daily_readings(JULY_CHANNEL, date(2026, 7, 1), date(2026, 7, 24))

    query = query_of(route.calls.last.request)
    assert query["period"] == ["month"], "only a month period yields daily buckets"
    assert query["diagramType"] == ["consumption"]
    assert query["obis"] == ["1-0:1.8.0"]


@respx.mock(assert_all_called=False)
async def test_values_outside_the_requested_range_are_dropped(respx_mock, client):
    """The portal always returns the whole calendar month."""
    respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=json_response("diagram_result_july2026.json")
    )

    async with client:
        readings = await client.get_daily_readings(
            JULY_CHANNEL, date(2026, 7, 10), date(2026, 7, 12)
        )

    assert [r.day.isoformat() for r in readings] == ["2026-07-10", "2026-07-11", "2026-07-12"]


@respx.mock(assert_all_called=False)
async def test_a_multi_month_range_is_split_into_one_request_per_month(respx_mock, client):
    route = respx_mock.route(DIAGRAM_RESULT).mock(
        side_effect=[
            diagram_payload(daily(date(2026, 5, 1), 31)),
            diagram_payload(daily(date(2026, 6, 1), 30)),
            diagram_payload(daily(date(2026, 7, 1), 31)),
        ]
    )

    async with client:
        readings = await client.get_daily_readings(
            JULY_CHANNEL, date(2026, 5, 20), date(2026, 7, 3)
        )

    assert route.call_count == 3
    assert len(readings) == 12 + 30 + 3
    assert readings[0].day == date(2026, 5, 20)
    assert readings[-1].day == date(2026, 7, 3)


@respx.mock(assert_all_called=False)
async def test_days_repeated_across_chunks_are_collapsed(respx_mock, client):
    overlapping = diagram_payload(daily(date(2026, 6, 1), 30))
    respx_mock.route(DIAGRAM_RESULT).mock(
        side_effect=[overlapping, diagram_payload(daily(date(2026, 6, 1), 30))]
    )

    async with client:
        readings = await client.get_daily_readings(
            JULY_CHANNEL, date(2026, 6, 1), date(2026, 7, 31)
        )

    assert len({r.day for r in readings}) == len(readings)


@respx.mock(assert_all_called=False)
async def test_a_range_crossing_the_dst_switch_keeps_one_reading_per_day(respx_mock, client):
    """On 2026-10-25 local midnight moves from 22:00 UTC to 23:00 UTC."""
    respx_mock.route(DIAGRAM_RESULT).mock(
        return_value=diagram_payload(daily(date(2026, 10, 20), 12))
    )

    async with client:
        readings = await client.get_daily_readings(
            JULY_CHANNEL, date(2026, 10, 20), date(2026, 10, 31)
        )

    by_day = {r.day: r for r in readings}
    assert len(readings) == 12, "no day may be lost or duplicated across the switch"
    assert all(r.start.hour == 0 for r in readings), "every value must sit on local midnight"

    summer = by_day[date(2026, 10, 24)].start.utcoffset()
    winter = by_day[date(2026, 10, 26)].start.utcoffset()
    assert summer == timedelta(hours=2)
    assert winter == timedelta(hours=1)


async def test_an_inverted_date_range_is_rejected(client):
    with pytest.raises(ValueError, match="after"):
        await client.get_daily_readings(JULY_CHANNEL, date(2026, 7, 10), date(2026, 7, 1))


# ------------------------------------------------------------- month split


def test_month_windows_covers_a_single_partial_month():
    assert _month_windows(date(2026, 7, 10), date(2026, 7, 12)) == [
        (date(2026, 7, 10), date(2026, 7, 12))
    ]


def test_month_windows_clips_the_first_and_last_month():
    assert _month_windows(date(2026, 5, 20), date(2026, 7, 3)) == [
        (date(2026, 5, 20), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 3)),
    ]


def test_month_windows_handles_the_turn_of_the_year():
    assert _month_windows(date(2026, 12, 30), date(2027, 1, 2)) == [
        (date(2026, 12, 30), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 1, 2)),
    ]


def test_month_windows_handles_a_leap_february():
    assert _month_windows(date(2028, 2, 1), date(2028, 2, 29))[-1][1] == date(2028, 2, 29)


def test_month_windows_covers_a_single_day():
    assert _month_windows(date(2026, 7, 5), date(2026, 7, 5)) == [
        (date(2026, 7, 5), date(2026, 7, 5))
    ]
