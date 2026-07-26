"""Long-term statistics import.

This is the part that feeds the Energy dashboard. Errors here are silent —
a wrong running sum or a misaligned bucket produces a plausible-looking graph
that is simply wrong — so the arithmetic is pinned down explicitly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from functools import partial

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period
from homeassistant.components.recorder.util import get_instance
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.plusportal.coordinator import MeterData
from custom_components.plusportal.statistics import (
    async_publish_statistics,
    hourly_totals,
    statistic_id,
)
from pyplusportal.const import PORTAL_TZ
from pyplusportal.cost import Tariff
from pyplusportal.models import Reading, ValueState

from .conftest import quarter_hours

ENERGY_ID = "plusportal:123456_10001_1000_import_energy"
COST_ID = "plusportal:123456_10001_1000_import_cost"


async def flush(hass: HomeAssistant) -> None:
    """Wait for the recorder to persist what was just imported.

    Statistics are written through the recorder's queue. Two refreshes are
    hours apart in reality, so a test that imports twice has to wait in
    between — otherwise the second import reads a running sum that has not
    been stored yet, and the assertion passes or fails on timing alone.
    """
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def read_sums(hass: HomeAssistant, stat_id: str) -> list[tuple[datetime, float]]:
    """Read back every imported statistic as (start, running sum)."""
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2020, 1, 1, tzinfo=PORTAL_TZ),
        None,
        {stat_id},
        "hour",
        None,
        {"sum", "state"},
    )
    return [
        (datetime.fromtimestamp(row["start"], tz=PORTAL_TZ), row["sum"])
        for row in rows.get(stat_id, [])
    ]


def meter_data(meter_point, readings: list[Reading]) -> dict[int, MeterData]:
    return {1000: MeterData(meter_point=meter_point, channel_readings={"1-0:1.8.0": readings})}


# ------------------------------------------------------------ bucketing


def test_quarter_hours_are_summed_into_hourly_buckets():
    """PP-HA-002: Home Assistant statistics are hourly; four readings make one bucket."""
    readings = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 8, kwh="0.25")

    totals = hourly_totals(readings)

    assert len(totals) == 2
    assert totals[datetime(2026, 7, 20, 0, tzinfo=PORTAL_TZ)] == Decimal("1.00")
    assert totals[datetime(2026, 7, 20, 1, tzinfo=PORTAL_TZ)] == Decimal("1.00")


def test_bucket_keys_are_aligned_to_the_hour():
    """PP-HA-002."""
    readings = quarter_hours(datetime(2026, 7, 20, 0, 15, tzinfo=PORTAL_TZ), 4)

    for bucket in hourly_totals(readings):
        assert (bucket.minute, bucket.second, bucket.microsecond) == (0, 0, 0)


def test_provisional_values_are_left_out():
    """PP-HA-006: They are replaced later; importing them invents consumption."""
    billable = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.25")
    provisional = quarter_hours(
        datetime(2026, 7, 20, 1, tzinfo=PORTAL_TZ), 4, kwh="9", state=ValueState.PRELIMINARY
    )

    totals = hourly_totals(billable + provisional)

    assert len(totals) == 1
    assert totals[datetime(2026, 7, 20, tzinfo=PORTAL_TZ)] == Decimal("1.00")


def test_substitute_values_are_included_because_they_are_billed():
    readings = quarter_hours(
        datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.25", state=ValueState.SUBSTITUTE
    )

    assert hourly_totals(readings)[datetime(2026, 7, 20, tzinfo=PORTAL_TZ)] == Decimal("1.00")


def test_a_dst_transition_does_not_collapse_two_hours_into_one():
    """On 2026-10-25 local 02:00 happens twice; the buckets must stay distinct."""
    before = datetime(2026, 10, 25, 1, 45, tzinfo=PORTAL_TZ)
    readings = quarter_hours(before, 12, kwh="0.25")

    totals = hourly_totals(readings)

    assert len({bucket.utcoffset() for bucket in totals}) == 2
    assert len(totals) == len({bucket.timestamp() for bucket in totals})


def test_statistic_ids_are_namespaced_to_the_integration():
    """External statistics must carry a domain prefix or the recorder rejects them."""
    assert (
        statistic_id("123456-10001", 1000, "import", "energy")
        == "plusportal:123456_10001_1000_import_energy"
    )


# -------------------------------------------------------------- import


async def test_energy_statistics_are_written_with_a_running_sum(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-001."""
    config_entry.add_to_hass(hass)
    readings = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 12, kwh="0.25")

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, readings), None)

    sums = await read_sums(hass, ENERGY_ID)
    assert [value for _, value in sums] == [1.0, 2.0, 3.0]
    assert sums[0][0] == datetime(2026, 7, 20, 0, tzinfo=PORTAL_TZ)


async def test_a_second_import_continues_the_sum_instead_of_restarting(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-003: The rolling correction window overlaps what is already stored."""
    config_entry.add_to_hass(hass)
    first = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 8, kwh="0.25")
    later = quarter_hours(datetime(2026, 7, 20, 2, tzinfo=PORTAL_TZ), 8, kwh="0.25")

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, first), None)
    await flush(hass)
    await async_publish_statistics(hass, config_entry, meter_data(meter_point, later), None)

    sums = await read_sums(hass, ENERGY_ID)
    assert [value for _, value in sums] == [1.0, 2.0, 3.0, 4.0]


async def test_a_corrected_value_replaces_the_old_one_without_duplicating_it(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-004."""
    config_entry.add_to_hass(hass)
    original = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.25")
    corrected = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.5")

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, original), None)
    await flush(hass)
    await async_publish_statistics(hass, config_entry, meter_data(meter_point, corrected), None)

    sums = await read_sums(hass, ENERGY_ID)
    assert len(sums) == 1, "the same hour must not appear twice"
    assert sums[0][1] == 2.0


async def test_re_importing_an_overlapping_window_does_not_double_count(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-003: The window's first hours already exist; their sums must not be added twice."""
    config_entry.add_to_hass(hass)
    full = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 16, kwh="0.25")
    overlap = full[4:]  # hours 1..3, all of which were already imported

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, full), None)
    await flush(hass)
    await async_publish_statistics(hass, config_entry, meter_data(meter_point, overlap), None)

    sums = await read_sums(hass, ENERGY_ID)
    assert [value for _, value in sums] == [1.0, 2.0, 3.0, 4.0]


async def test_no_billable_readings_writes_nothing_rather_than_a_zero_row(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    config_entry.add_to_hass(hass)
    provisional = quarter_hours(
        datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, state=ValueState.PRELIMINARY
    )

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, provisional), None)

    assert await read_sums(hass, ENERGY_ID) == []


# ---------------------------------------------------------------- cost


async def test_no_cost_statistics_without_a_tariff(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    config_entry.add_to_hass(hass)
    readings = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.25")

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, readings), None)

    assert await read_sums(hass, COST_ID) == []


async def test_cost_statistics_price_each_hour(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """1 kWh at 34.5 ct is 0.345 EUR."""
    config_entry.add_to_hass(hass)
    readings = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 8, kwh="0.25")
    tariff = Tariff(energy_price_ct_per_kwh=Decimal("34.5"), base_price_eur_per_year=Decimal("120"))

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, readings), tariff)

    sums = await read_sums(hass, COST_ID)
    assert [round(value, 4) for _, value in sums] == [0.345, 0.69]


async def test_a_disabled_recorder_is_not_an_error(
    hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-007: The recorder is optional in Home Assistant; consumption sensors still work."""
    config_entry.add_to_hass(hass)
    readings = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.25")

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, readings), None)


def test_statistic_ids_are_scoped_to_the_account() -> None:
    """PP-HA-017: two accounts must not share a statistic series.

    The meter point id is only unique inside one portal account. Two
    configured accounts — a second household, or a second utility — can each
    have a meter point 5821, and without the account in the id their energy
    would be summed into one series.
    """
    first = statistic_id("123456-10001", 5821, "import", "energy")
    second = statistic_id("654321-20002", 5821, "import", "energy")

    assert first != second


def test_a_statistic_id_is_accepted_by_home_assistant() -> None:
    """PP-HA-017: the recorder rejects ids outside its own grammar."""
    from homeassistant.components.recorder.statistics import valid_statistic_id

    assert valid_statistic_id(statistic_id("123456-10001", 5821, "import", "energy"))
    assert valid_statistic_id(statistic_id("123456-10001", 5821, "import", "cost"))


async def test_the_statistic_name_says_what_it_is(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-025: the Energy dashboard picker lists it by name alone.

    "<meter> energy" sorts under the meter number, away from everything else,
    and says nothing in a non-English interface. The dashboard's own term for
    the field it belongs in is "grid consumption".
    """
    config_entry.add_to_hass(hass)
    readings = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.25")

    await async_publish_statistics(hass, config_entry, meter_data(meter_point, readings), None)
    await flush(hass)

    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_source="plusportal")
    )
    names = {meta["statistic_id"]: meta["name"] for _, meta in metadata.values()}

    assert names[ENERGY_ID] == "1ABC0000000000* grid consumption", names


def test_a_channel_slug_is_readable_for_the_codes_that_matter() -> None:
    """PP-HA-026: import and export must be told apart at a glance."""
    from custom_components.plusportal.statistics import channel_slug

    assert channel_slug("1-0:1.8.0") == "import"
    assert channel_slug("1-0:2.8.0") == "export"


def test_an_unknown_obis_code_still_yields_a_usable_slug() -> None:
    """PP-HA-026: the recorder only accepts lowercase alphanumerics."""
    from homeassistant.components.recorder.statistics import valid_statistic_id

    from custom_components.plusportal.statistics import channel_slug

    slug = channel_slug("7-20:99.33.0")

    assert slug == "7_20_99_33_0"
    assert valid_statistic_id(statistic_id("123456-10001", 1000, slug, "energy"))


async def test_import_and_export_do_not_share_a_series(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-026: summing them would overstate what was drawn from the grid.

    A meter with feed-in reports two channels. Merging them makes the Energy
    dashboard show a grid draw that includes energy the household exported —
    silently, as a number that merely looks high.
    """
    config_entry.add_to_hass(hass)
    start = datetime(2026, 7, 20, tzinfo=PORTAL_TZ)
    data = {
        1000: MeterData(
            meter_point=meter_point,
            channel_readings={
                "1-0:1.8.0": quarter_hours(start, 4, kwh="0.25"),
                "1-0:2.8.0": quarter_hours(start, 4, kwh="1.00"),
            },
        )
    }

    await async_publish_statistics(hass, config_entry, data, None)
    await flush(hass)

    imported = await read_sums(hass, "plusportal:123456_10001_1000_import_energy")
    exported = await read_sums(hass, "plusportal:123456_10001_1000_export_energy")

    assert [v for _, v in imported] == [1.0], "1 kWh drawn"
    assert [v for _, v in exported] == [4.0], "4 kWh fed in"


async def test_a_single_channel_meter_gets_one_series(
    recorder_mock: Recorder, hass: HomeAssistant, config_entry: MockConfigEntry, meter_point
) -> None:
    """PP-HA-026: the common case stays a single, obvious series."""
    config_entry.add_to_hass(hass)
    data = {
        1000: MeterData(
            meter_point=meter_point,
            channel_readings={
                "1-0:1.8.0": quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 4, kwh="0.25")
            },
        )
    }

    await async_publish_statistics(hass, config_entry, data, None)
    await flush(hass)

    assert [v for _, v in await read_sums(hass, "plusportal:123456_10001_1000_import_energy")] == [
        1.0
    ]
