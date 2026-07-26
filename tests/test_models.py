"""Parsing of raw portal payloads into the library's domain model."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from pyplusportal.exceptions import ParseError
from pyplusportal.models import (
    Channel,
    MeterPoint,
    Overview,
    Reading,
    Session,
    Taf,
    ValueState,
)

# ---------------------------------------------------------------- ValueState


def test_true_and_substitute_values_are_billable():
    """PP-EXT-008."""
    assert ValueState.TRUE_VALUE.billable
    assert ValueState.SUBSTITUTE.billable


def test_preliminary_values_are_not_billable():
    assert not ValueState.PRELIMINARY.billable


def test_unknown_state_code_parses_to_unknown_and_is_not_billable():
    """PP-EXT-008."""
    assert ValueState.parse("X") is ValueState.UNKNOWN
    assert not ValueState.UNKNOWN.billable


def test_missing_state_code_parses_to_unknown():
    assert ValueState.parse(None) is ValueState.UNKNOWN


# ------------------------------------------------------------------- Reading


def _point(**overrides):
    point = {
        "date": 1782856800000,  # 2026-07-01 00:00 Europe/Berlin
        "value": Decimal("0.0312"),
        "unitA": "kWh",
        "state": "W",
    }
    point.update(overrides)
    return point


def test_reading_timestamp_is_local_midnight_in_portal_timezone():
    """PP-EXT-010."""
    reading = Reading.from_daily_api(_point(), obis="1-0:1.8.0")

    assert reading.start.isoformat() == "2026-07-01T00:00:00+02:00"
    assert reading.day.isoformat() == "2026-07-01"


def test_reading_keeps_full_decimal_precision():
    """PP-EXT-009."""
    reading = Reading.from_daily_api(_point(value=Decimal("0.018321")), obis="1-0:1.8.0")

    assert reading.value == Decimal("0.018321")
    assert isinstance(reading.value, Decimal)


def test_reading_falls_back_to_value_a_when_value_is_absent():
    raw = _point()
    del raw["value"]
    raw["valueA"] = Decimal("0.5")

    assert Reading.from_daily_api(raw, obis="1-0:1.8.0").value == Decimal("0.5")


def test_reading_without_any_value_is_rejected():
    """PP-EXT-017."""
    raw = _point()
    del raw["value"]

    with pytest.raises(ParseError, match="value"):
        Reading.from_daily_api(raw, obis="1-0:1.8.0")


def test_reading_without_timestamp_is_rejected():
    raw = _point()
    del raw["date"]

    with pytest.raises(ParseError, match="date"):
        Reading.from_daily_api(raw, obis="1-0:1.8.0")


def test_reading_carries_its_quality_flag():
    substitute = Reading.from_daily_api(_point(state="E"), obis="1-0:1.8.0")
    assert substitute.state is ValueState.SUBSTITUTE
    assert not Reading.from_daily_api(_point(state="V"), obis="1-0:1.8.0").billable


def test_readings_are_hashable_so_callers_can_deduplicate():
    a = Reading.from_daily_api(_point(), obis="1-0:1.8.0")
    b = Reading.from_daily_api(_point(), obis="1-0:1.8.0")

    assert len({a, b}) == 1


# ---------------------------------------------------------------- MeterPoint


def test_meter_points_are_flattened_out_of_the_grouped_response(user_item_list):
    points = MeterPoint.list_from_api(user_item_list)

    assert len(points) == 1
    point = points[0]
    assert point.id == 1000
    assert point.name == "1ABC0000000000*"
    assert point.category == "Electricity"
    assert point.device_type == "gwa"
    assert point.source_type == "ROBOTRON"


def test_meter_point_exposes_only_active_tariff_use_cases(user_item_list):
    point = MeterPoint.list_from_api(user_item_list)[0]

    assert {taf.number for taf in point.tafs} == {55789, 6532, 55788}
    assert {taf.number for taf in point.active_tafs} == {55789, 6532}


def test_meter_point_prefers_the_highest_resolution_tariff_use_case(user_item_list):
    """PP-EXT-016: TAF-7 (Zählerstandsgangmessung) carries finer data than TAF-1 (datensparsam)."""
    point = MeterPoint.list_from_api(user_item_list)[0]

    assert point.primary_taf is not None
    assert point.primary_taf.number == 55789
    assert point.primary_taf.type == 7
    assert point.primary_taf.obis == ["1-0:1.8.0"]


def test_meter_point_without_any_active_taf_has_no_primary(user_item_list):
    for taf in user_item_list[0]["userItems"][0]["tafs"]:
        taf["status"] = 0

    assert MeterPoint.list_from_api(user_item_list)[0].primary_taf is None


def test_meter_point_without_an_id_is_rejected(user_item_list):
    """PP-EXT-017."""
    del user_item_list[0]["userItems"][0]["id"]

    with pytest.raises(ParseError, match="id"):
        MeterPoint.list_from_api(user_item_list)


# ------------------------------------------------------------------ Overview


def test_overview_exposes_month_totals_and_data_range(overview_payload):
    overviews = Overview.list_from_api(overview_payload)

    assert len(overviews) == 1
    ov = overviews[0]
    assert ov.meter_point_id == 1000
    assert ov.obis == "1-0:1.8.0"
    assert ov.label == "Stromverbrauch"
    assert ov.unit == "kWh"
    assert ov.this_month_sum == Decimal("0.757899")
    assert ov.prev_month_sum == Decimal("0.587")


def test_overview_converts_data_boundaries_to_local_datetimes(overview_payload):
    ov = Overview.list_from_api(overview_payload)[0]

    assert ov.first_value_at.isoformat() == "2026-06-18T00:00:00+02:00"
    assert ov.last_value_at.isoformat() == "2026-07-25T01:00:00+02:00"


def test_overview_tolerates_absent_totals(overview_payload):
    overview_payload[0]["data"][0]["prevMonthSum"] = None

    assert Overview.list_from_api(overview_payload)[0].prev_month_sum is None


# ------------------------------------------------------------------- Channel


def test_channels_are_read_from_the_diagram_configuration(diagram_config):
    channels = Channel.list_from_api(diagram_config, meter_point_id=1000, taf_number=55789)

    assert len(channels) == 1
    channel = channels[0]
    assert channel.obis == "1-0:1.8.0"
    assert channel.label == "Stromverbrauch"
    assert channel.unit == "kWh"
    assert channel.periods == ("DAY",)


def test_channel_reports_the_range_for_which_data_exists(diagram_config):
    channel = Channel.list_from_api(diagram_config, meter_point_id=1000, taf_number=55789)[0]

    assert channel.available_from.isoformat() == "2026-06-18T00:00:00+02:00"
    assert channel.available_to.date().isoformat() == "2026-07-25"


def test_duplicate_channels_across_config_entries_are_collapsed(diagram_config):
    diagram_config.append(diagram_config[0])

    channels = Channel.list_from_api(diagram_config, meter_point_id=1000, taf_number=55789)

    assert len(channels) == 1


# ------------------------------------------------------------------- Session


def test_session_reports_its_expiry(session_payload):
    session = Session.from_api(session_payload)

    assert session.user_id == 10001
    assert session.username == "1000000000"
    assert session.valid_from.isoformat() == "2026-07-25T14:29:06.113000+02:00"
    assert session.expires_at.isoformat() == "2026-07-25T15:35:39.957000+02:00"


def test_session_is_expired_shortly_before_the_portal_would_drop_it(session_payload):
    session = Session.from_api(session_payload)
    just_inside = session.expires_at - timedelta(minutes=2)
    well_inside = session.expires_at - timedelta(minutes=30)

    assert session.is_expired(now=just_inside)
    assert not session.is_expired(now=well_inside)


def test_session_without_a_user_id_is_rejected(session_payload):
    del session_payload["id"]

    with pytest.raises(ParseError, match="id"):
        Session.from_api(session_payload)


def test_session_knows_whether_the_energy_data_feature_is_enabled(session_payload):
    assert Session.from_api(session_payload).has_energy_data

    session_payload["features"] = []
    assert not Session.from_api(session_payload).has_energy_data


def test_naive_datetimes_are_never_produced(session_payload, overview_payload):
    """PP-EXT-010."""
    session = Session.from_api(session_payload)
    overview = Overview.list_from_api(overview_payload)[0]

    for value in (session.expires_at, overview.first_value_at, overview.last_value_at):
        assert isinstance(value, datetime)
        assert value.tzinfo is not None


# ---------------------------------------------------------------------- Taf


def _taf(label: str) -> Taf:
    return Taf(number=55789, type=7, label=label, obis=["1-0:1.8.0"], active=True)


def test_a_tariff_use_case_title_explains_its_leading_number():
    """The portal prefixes the standardised TAF number; on its own it reads as noise."""
    assert _taf("07: Zählerstandsgangmessung").title == "Zählerstandsgangmessung (TAF 7)"


def test_a_single_digit_tariff_use_case_is_not_padded():
    assert _taf("01: Datensparsamer Tarif").title == "Datensparsamer Tarif (TAF 1)"


def test_a_label_without_a_number_is_left_alone():
    assert _taf("Zählerstandsgangmessung").title == "Zählerstandsgangmessung"


def test_an_empty_label_falls_back_to_the_number():
    assert _taf("").title == "TAF 7"
