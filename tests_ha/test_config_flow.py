"""Setting the integration up, and reconfiguring its tariff afterwards."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.plusportal.const import (
    CONF_BASE_PRICE,
    CONF_BILLING_YEAR_START,
    CONF_ENERGY_PRICE,
    CONF_MONTHLY_ADVANCE,
    CONF_TENANT,
    DOMAIN,
)
from pyplusportal.exceptions import AuthenticationError, PortalUnavailableError

pytestmark = pytest.mark.usefixtures("custom_integration")

USER_INPUT = {
    CONF_TENANT: "123456",
    "username": "1000000000",
    "password": "s3cret",
}


@pytest.fixture
def client_ok(meter_point, session):
    """Serve a portal that accepts the credentials and has one metering point."""
    with patch(
        "custom_components.plusportal.config_flow.PlusPortalClient", autospec=True
    ) as factory:
        client = factory.return_value.__aenter__.return_value
        client.login = AsyncMock(return_value=session)
        client.get_meter_points = AsyncMock(return_value=[meter_point])
        yield client


async def test_the_form_is_shown_first(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_valid_credentials_create_an_entry(hass: HomeAssistant, client_ok) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PlusPortal 123456"
    assert result["data"][CONF_TENANT] == "123456"
    assert result["data"]["password"] == "s3cret"


async def test_the_entry_is_keyed_on_tenant_and_user(hass: HomeAssistant, client_ok) -> None:
    """So the same account cannot be added twice, but two accounts can coexist."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "123456-10001"


async def test_the_same_account_cannot_be_added_twice(hass: HomeAssistant, client_ok) -> None:
    for _ in range(2):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("nope"), "invalid_auth"),
        (PortalUnavailableError("down"), "cannot_connect"),
    ],
)
async def test_login_failures_are_reported_on_the_form(
    hass: HomeAssistant, error: Exception, expected: str
) -> None:
    with patch(
        "custom_components.plusportal.config_flow.PlusPortalClient", autospec=True
    ) as factory:
        factory.return_value.__aenter__.return_value.login = AsyncMock(side_effect=error)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_an_unusable_tenant_is_reported_on_the_form(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_TENANT: "not a portal"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TENANT: "invalid_tenant"}


async def test_an_account_without_any_metering_point_is_rejected(
    hass: HomeAssistant, session
) -> None:
    """PP-HA-009: Setting it up would silently produce an integration with no entities."""
    with patch(
        "custom_components.plusportal.config_flow.PlusPortalClient", autospec=True
    ) as factory:
        client = factory.return_value.__aenter__.return_value
        client.login = AsyncMock(return_value=session)
        client.get_meter_points = AsyncMock(return_value=[])

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_meters"}


# ------------------------------------------------------------------ reauth


async def test_a_rejected_session_starts_a_reauth_flow(
    hass: HomeAssistant, config_entry, client_ok
) -> None:
    config_entry.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"


async def test_reauth_updates_the_password_without_a_second_entry(
    hass: HomeAssistant, config_entry, client_ok
) -> None:
    """PP-HA-012."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == "new-password"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


# ----------------------------------------------------------------- options


async def test_the_tariff_can_be_configured_afterwards(hass: HomeAssistant, config_entry) -> None:
    """A tariff change must not require removing and re-adding the integration."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENERGY_PRICE: 34.5,
            CONF_BASE_PRICE: 120.0,
            CONF_MONTHLY_ADVANCE: 50.0,
            CONF_BILLING_YEAR_START: "01-01",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_ENERGY_PRICE] == 34.5


async def test_an_invalid_billing_year_start_is_rejected(hass: HomeAssistant, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENERGY_PRICE: 34.5, CONF_BILLING_YEAR_START: "02-29"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BILLING_YEAR_START: "invalid_billing_year_start"}


async def test_the_tariff_is_optional(hass: HomeAssistant, config_entry) -> None:
    """Consumption tracking must work without any prices configured."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
