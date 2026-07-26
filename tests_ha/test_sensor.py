"""The entities the integration exposes."""

from __future__ import annotations

from datetime import datetime
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

    assert hass.states.get("sensor.1abc0000000000_projected_cost") is None


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

    assert hass.states.get("sensor.1abc0000000000_projected_cost") is not None
    assert hass.states.get("sensor.1abc0000000000_expected_settlement") is not None
    assert hass.states.get("sensor.1abc0000000000_cost_this_billing_year") is not None


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

    assert float(hass.states.get("sensor.1abc0000000000_expected_settlement").state) < 0


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
