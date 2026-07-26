"""Fixtures for the Home Assistant integration suite."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.plusportal.const import CONF_TENANT, DOMAIN
from pyplusportal.const import PORTAL_TZ
from pyplusportal.models import (
    Channel,
    MeterPoint,
    Overview,
    Reading,
    Session,
    Taf,
    ValueState,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def custom_integration(enable_custom_integrations: None) -> Generator[None, None, None]:
    """Let Home Assistant discover `custom_components/plusportal`.

    Deliberately not autouse: `enable_custom_integrations` pulls in the `hass`
    fixture, and a test that also needs the recorder must set the recorder up
    first. Tests that load the integration request this explicitly.
    """
    yield


@pytest.fixture
def session() -> Session:
    """Return a live portal session for the account under test."""
    now = datetime.now(tz=UTC)
    return Session(
        user_id=10001,
        username="1000000000",
        valid_from=now,
        expires_at=now + timedelta(hours=1),
        features=("energydataview",),
    )


@pytest.fixture
def meter_point() -> MeterPoint:
    """One electricity metering point with an active quarter-hourly TAF."""
    return MeterPoint(
        id=1000,
        name="1ABC0000000000*",
        category="Electricity",
        device_type="gwa",
        source_type="ROBOTRON",
        tafs=(
            Taf(
                number=55789,
                type=7,
                label="07: Zählerstandsgangmessung",
                obis=["1-0:1.8.0"],
                active=True,
            ),
        ),
    )


@pytest.fixture
def channel() -> Channel:
    """Return the consumption channel of that metering point."""
    return Channel(
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
def overview() -> Overview:
    """Return the portal's dashboard figures, matching the recorded account."""
    return Overview(
        meter_point_id=1000,
        obis="1-0:1.8.0",
        label="Stromverbrauch",
        unit="kWh",
        this_month_sum=Decimal("0.757899"),
        prev_month_sum=Decimal("0.587"),
        first_value_at=datetime(2026, 6, 18, tzinfo=PORTAL_TZ),
        last_value_at=datetime(2026, 7, 25, 1, 0, tzinfo=PORTAL_TZ),
    )


def quarter_hours(
    day: datetime, count: int, kwh: str = "0.01", state: ValueState = ValueState.TRUE_VALUE
) -> list[Reading]:
    """Build consecutive quarter-hourly readings starting at `day`."""
    return [
        Reading(
            start=day + timedelta(minutes=15 * index),
            duration=timedelta(minutes=15),
            value=Decimal(kwh),
            unit="kWh",
            obis="1-0:1.8.0",
            state=state,
        )
        for index in range(count)
    ]


@pytest.fixture
def readings() -> list[Reading]:
    """Return a full day of quarter-hourly readings, 0.96 kWh in total."""
    return quarter_hours(datetime(2026, 7, 20, tzinfo=PORTAL_TZ), 96)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a configured integration entry with no tariff set."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="PlusPortal 123456",
        unique_id="123456-10001",
        data={
            CONF_TENANT: "123456",
            "username": "1000000000",
            "password": "s3cret",
        },
        options={},
    )
