"""The entities the integration exposes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.plusportal.const import (
    CONF_BASE_PRICE,
    CONF_ENERGY_PRICE,
    CONF_MONTHLY_ADVANCE,
    DOMAIN,
)
from pyplusportal.const import PORTAL_TZ
from pyplusportal.models import ValueState

from .conftest import quarter_hours

pytestmark = pytest.mark.usefixtures("custom_integration")


@pytest.fixture
def portal(meter_point, channel, overview, session):
    """Serve one meter with a full day of quarter-hourly data."""
    readings = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 96, kwh="0.01")
    with patch(
        "custom_components.plusportal.coordinator.PlusPortalClient", autospec=True
    ) as factory:
        client = factory.return_value
        client.login = AsyncMock(return_value=session)
        client.get_overview = AsyncMock(return_value=[overview])
        client.get_meter_points = AsyncMock(return_value=[meter_point])
        client.get_channels = AsyncMock(return_value=[channel])
        client.get_interval_readings = AsyncMock(return_value=readings)
        client.aclose = AsyncMock()
        # so a test can assert on how the client was constructed
        client.factory = factory
        yield client


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up the config entry."""
    entry.add_to_hass(hass)
    with patch("custom_components.plusportal.statistics.async_add_external_statistics"):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_integration_sets_up(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    await setup_entry(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.async_entity_ids("sensor")


async def test_consumption_sensors_report_the_portals_figures(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    await setup_entry(hass, config_entry)

    assert hass.states.get("sensor.1abc0000000000_this_month").state == "0.757899"
    assert hass.states.get("sensor.1abc0000000000_previous_month").state == "0.587"


async def test_the_last_day_sensor_reports_the_most_recent_complete_day(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """96 quarter-hours of 0.01 kWh is 0.96 kWh."""
    await setup_entry(hass, config_entry)

    state = hass.states.get("sensor.1abc0000000000_last_day")
    assert state.state == "0.96"
    assert state.attributes["date"] == "2026-07-20"


async def test_the_last_measurement_sensor_carries_a_timestamp(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    await setup_entry(hass, config_entry)

    state = hass.states.get("sensor.1abc0000000000_last_measurement")
    assert state.attributes["device_class"] == "timestamp"
    assert state.state.startswith("2026-07-24T23:00")


async def test_data_quality_reports_the_share_of_final_values(
    hass: HomeAssistant, config_entry: MockConfigEntry, meter_point, channel, overview, session
) -> None:
    """Half true values, half provisional, is 50 %."""
    mixed = quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 48) + quarter_hours(
        datetime(2026, 7, 20, 12, tzinfo=PORTAL_TZ), 48, state=ValueState.PRELIMINARY
    )
    with patch(
        "custom_components.plusportal.coordinator.PlusPortalClient", autospec=True
    ) as factory:
        client = factory.return_value
        client.login = AsyncMock(return_value=session)
        client.get_overview = AsyncMock(return_value=[overview])
        client.get_meter_points = AsyncMock(return_value=[meter_point])
        client.get_channels = AsyncMock(return_value=[channel])
        client.get_interval_readings = AsyncMock(return_value=mixed)
        client.aclose = AsyncMock()
        await setup_entry(hass, config_entry)

    assert hass.states.get("sensor.1abc0000000000_data_quality").state == "50.0"


# ------------------------------------------------------------------- cost


async def test_no_cost_sensors_without_a_tariff(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-011: Cost entities with no prices behind them would only show zeros."""
    await setup_entry(hass, config_entry)

    assert hass.states.get("sensor.1abc0000000000_projected_cost_billing_year") is None


async def test_cost_sensors_appear_once_a_tariff_is_configured(
    hass: HomeAssistant, meter_point, portal
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PlusPortal 123456",
        unique_id="123456-10001",
        data={"tenant": "123456", "username": "1000000000", "password": "s3cret"},
        options={
            CONF_ENERGY_PRICE: 34.5,
            CONF_BASE_PRICE: 120.0,
            CONF_MONTHLY_ADVANCE: 50.0,
        },
    )
    await setup_entry(hass, entry)

    assert hass.states.get("sensor.1abc0000000000_projected_cost_billing_year") is not None
    assert hass.states.get("sensor.1abc0000000000_expected_settlement_billing_year") is not None
    assert hass.states.get("sensor.1abc0000000000_cost_to_date") is not None


async def test_the_settlement_sensor_says_which_way_the_money_flows(
    hass: HomeAssistant, meter_point, portal
) -> None:
    """A negative settlement is a refund, and the sign must survive."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456-10001",
        data={"tenant": "123456", "username": "1000000000", "password": "s3cret"},
        options={CONF_ENERGY_PRICE: 1.0, CONF_MONTHLY_ADVANCE: 500.0},
    )
    await setup_entry(hass, entry)

    assert (
        float(hass.states.get("sensor.1abc0000000000_expected_settlement_billing_year").state) < 0
    )


# --------------------------------------------------------------- registry


async def test_all_entities_belong_to_one_device_per_meter(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-010."""
    await setup_entry(hass, config_entry)

    devices = dr.async_get(hass).devices.get_devices_for_config_entry_id(config_entry.entry_id)
    assert len(devices) == 1
    assert devices[0].name == "1ABC0000000000*"


async def test_entities_have_stable_unique_ids(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-010: Without them, renaming the meter would orphan the history."""
    await setup_entry(hass, config_entry)

    registry = er.async_get(hass)
    entry = registry.async_get("sensor.1abc0000000000_this_month")
    assert entry.unique_id == "123456-10001_1000_this_month"


async def test_diagnostic_entities_are_categorised_as_such(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    await setup_entry(hass, config_entry)

    entry = er.async_get(hass).async_get("sensor.1abc0000000000_data_quality")
    assert entry.entity_category == er.EntityCategory.DIAGNOSTIC


# --------------------------------------------------------------- failures


async def test_missing_data_leaves_sensors_unknown_rather_than_zero(
    hass: HomeAssistant, config_entry: MockConfigEntry, meter_point, channel, session
) -> None:
    """PP-HA-008: Reporting 0 kWh would look like real consumption of nothing."""
    with patch(
        "custom_components.plusportal.coordinator.PlusPortalClient", autospec=True
    ) as factory:
        client = factory.return_value
        client.login = AsyncMock(return_value=session)
        client.get_overview = AsyncMock(return_value=[])
        client.get_meter_points = AsyncMock(return_value=[meter_point])
        client.get_channels = AsyncMock(return_value=[channel])
        client.get_interval_readings = AsyncMock(return_value=[])
        client.aclose = AsyncMock()
        await setup_entry(hass, config_entry)

    assert hass.states.get("sensor.1abc0000000000_this_month").state == STATE_UNKNOWN
    assert hass.states.get("sensor.1abc0000000000_last_day").state == STATE_UNKNOWN


async def test_the_entry_unloads_cleanly(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    await setup_entry(hass, config_entry)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    portal.aclose.assert_awaited()


async def test_diagnostics_never_leak_the_password(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-013: Users paste this into bug reports."""
    from custom_components.plusportal.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await setup_entry(hass, config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert "s3cret" not in str(diagnostics)
    assert "1000000000" not in str(diagnostics)
    assert diagnostics["meters"][0]["readings"]["count"] == 96


async def test_the_device_is_labelled_without_the_raw_tariff_number(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """The portal writes 07: Zählerstandsgangmessung; spell that number out."""
    await setup_entry(hass, config_entry)

    device = dr.async_get(hass).devices.get_devices_for_config_entry_id(config_entry.entry_id)[0]

    assert device.model == "Zählerstandsgangmessung (TAF 7)"
    assert device.manufacturer == "PlusPortal", "we are not affiliated with the operator"


# ------------------------------------------------------- cost breakdown


@pytest.fixture
def tariff_entry() -> MockConfigEntry:
    """PP-HA-014: an entry priced at 35 ct/kWh with a 12 EUR/a standing charge."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456-10001",
        data={"tenant": "123456", "username": "1000000000", "password": "s3cret"},
        options={
            CONF_ENERGY_PRICE: 35.0,
            CONF_BASE_PRICE: 12.0,
            CONF_MONTHLY_ADVANCE: 80.0,
        },
    )


async def test_the_standing_charge_is_reported_separately(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-014: a single blended total hides what the Grundpreis contributes."""
    await setup_entry(hass, tariff_entry)

    assert hass.states.get("sensor.1abc0000000000_standing_charge_to_date") is not None


async def test_the_energy_component_is_reported_separately(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-014: so the total can be checked against its parts."""
    await setup_entry(hass, tariff_entry)

    assert hass.states.get("sensor.1abc0000000000_energy_cost_to_date") is not None


async def test_the_total_equals_energy_plus_standing_charge(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-014: figures that do not add up are worse than a rounded cent."""
    await setup_entry(hass, tariff_entry)

    energy = Decimal(hass.states.get("sensor.1abc0000000000_energy_cost_to_date").state)
    base = Decimal(hass.states.get("sensor.1abc0000000000_standing_charge_to_date").state)
    total = Decimal(hass.states.get("sensor.1abc0000000000_cost_to_date").state)

    assert energy + base == total


async def test_the_energy_price_is_exposed_as_an_entity(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-015: the Energy dashboard can attach a price entity to a source."""
    await setup_entry(hass, tariff_entry)

    state = hass.states.get("sensor.1abc0000000000_energy_price")
    assert state is not None
    assert state.state == "0.35", "35 ct/kWh is 0.35 EUR/kWh"
    assert state.attributes["unit_of_measurement"] == "EUR/kWh"


async def test_the_settlement_shows_what_has_been_paid_so_far(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-016: a settlement figure is meaningless without the advances behind it."""
    await setup_entry(hass, tariff_entry)

    attributes = hass.states.get(
        "sensor.1abc0000000000_expected_settlement_billing_year"
    ).attributes
    assert "advances_paid_eur" in attributes
    assert "advances_due_eur" in attributes


async def test_no_price_entity_without_a_tariff(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-015: an unpriced supply has no price to show."""
    await setup_entry(hass, config_entry)

    assert hass.states.get("sensor.1abc0000000000_energy_price") is None


async def test_the_http_client_comes_from_home_assistant(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-018: constructing one ourselves blocks the event loop.

    httpx.AsyncClient loads the CA bundle from disk when it is created. Doing
    that inside the loop stalls everything else in Home Assistant, and Home
    Assistant reports it as a bug in the integration.
    """
    await setup_entry(hass, config_entry)

    _, kwargs = portal.factory.call_args
    client = kwargs.get("client")
    assert client is not None, "let Home Assistant build the client"
    # httpx defaults to 5 seconds, and a month of quarter-hourly data takes
    # longer than that to come back. Home Assistant sets no timeout of its own,
    # so one has to be asked for explicitly or the backfill never completes.
    assert client.timeout.read is not None
    assert client.timeout.read >= 30, f"read timeout is only {client.timeout.read}s"


async def test_settlement_attributes_survive_being_stored(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-016: the recorder drops a state whose attributes are not JSON.

    Decimal is the right type for money everywhere else in this project, but
    Home Assistant serialises attributes to JSON and cannot carry it — the
    entity goes unavailable with only a recorder warning to show for it.
    """
    import json

    await setup_entry(hass, tariff_entry)

    attributes = hass.states.get(
        "sensor.1abc0000000000_expected_settlement_billing_year"
    ).attributes
    json.dumps(dict(attributes))


async def test_diagnostics_carry_the_cost_breakdown(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-019: a bug report about a wrong bill needs the figures behind it."""
    import json

    from custom_components.plusportal.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await setup_entry(hass, tariff_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, tariff_entry)

    cost = diagnostics["meters"][0]["cost"]
    assert cost["energy_eur"] is not None
    assert cost["standing_charge_eur"] is not None
    assert cost["projected_eur"] is not None
    assert cost["billing_year"]
    json.dumps(diagnostics), "diagnostics are downloaded as JSON"


async def test_diagnostics_state_plainly_when_no_tariff_is_set(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-019: absent prices must read as absent, not as zero cost."""
    from custom_components.plusportal.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await setup_entry(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["meters"][0]["cost"] is None


# ------------------------------------------------------ naming precision


async def test_every_cost_sensor_names_its_reference_period(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-020: an amount without a period is unreadable.

    1.25 EUR could be a month, a year or the part of the billing year that has
    elapsed. Only the last is true, and the name has to say so.
    """
    await setup_entry(hass, tariff_entry)

    periodic = {
        "sensor.1abc0000000000_energy_cost_to_date": "to date",
        "sensor.1abc0000000000_standing_charge_to_date": "to date",
        "sensor.1abc0000000000_cost_to_date": "to date",
        "sensor.1abc0000000000_projected_cost_billing_year": "billing year",
        "sensor.1abc0000000000_expected_settlement_billing_year": "billing year",
        "sensor.1abc0000000000_standing_charge_per_year": "per year",
    }
    for entity_id, expected in periodic.items():
        state = hass.states.get(entity_id)
        assert state is not None, f"{entity_id} missing"
        name = state.attributes["friendly_name"]
        assert expected in name.lower(), f"{name!r} does not say {expected!r}"


async def test_the_configured_standing_charge_is_shown(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-020: entering 12 EUR/a and never seeing it back is confusing."""
    await setup_entry(hass, tariff_entry)

    assert hass.states.get("sensor.1abc0000000000_standing_charge_per_year").state == "12.0"


async def test_cost_sensors_carry_the_billing_year_they_refer_to(
    hass: HomeAssistant, tariff_entry: MockConfigEntry, portal
) -> None:
    """PP-HA-020: the exact dates, for anyone checking the arithmetic."""
    await setup_entry(hass, tariff_entry)

    for key in (
        "energy_cost_to_date",
        "standing_charge_to_date",
        "cost_to_date",
        "projected_cost_billing_year",
    ):
        attributes = hass.states.get(f"sensor.1abc0000000000_{key}").attributes
        assert "billing_year_start" in attributes, key
        assert "billing_year_end" in attributes, key
