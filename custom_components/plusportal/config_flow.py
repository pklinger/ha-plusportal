"""Config and options flow for the PlusPortal integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from pyplusportal.client import PlusPortalClient, resolve_base_url
from pyplusportal.cost import Tariff
from pyplusportal.exceptions import AuthenticationError, PortalUnavailableError

from .const import (
    CONF_BASE_PRICE,
    CONF_BILLING_YEAR_START,
    CONF_ENERGY_PRICE,
    CONF_MONTHLY_ADVANCE,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_TENANT,
    DEFAULT_BILLING_YEAR_START,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    MAX_SCAN_INTERVAL_HOURS,
    MIN_SCAN_INTERVAL_HOURS,
)
from .tariff import parse_billing_year_start

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TENANT): TextSelector(),
        vol.Required(CONF_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _price_selector(maximum: float, step: float = 0.01) -> NumberSelector:
    """Build a number selector for a monetary field."""
    return NumberSelector(
        NumberSelectorConfig(min=0, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    )


def tariff_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Build the tariff form, pre-filled with whatever is already set.

    Shared by the setup flow and the options flow so the two can never offer
    different fields or validate differently.
    """
    return vol.Schema(
        {
            vol.Optional(
                CONF_ENERGY_PRICE,
                description={"suggested_value": current.get(CONF_ENERGY_PRICE)},
            ): _price_selector(200.0),
            vol.Optional(
                CONF_BASE_PRICE,
                description={"suggested_value": current.get(CONF_BASE_PRICE)},
            ): _price_selector(10_000.0),
            vol.Optional(
                CONF_MONTHLY_ADVANCE,
                description={"suggested_value": current.get(CONF_MONTHLY_ADVANCE)},
            ): _price_selector(10_000.0),
            vol.Optional(
                CONF_BILLING_YEAR_START,
                default=current.get(CONF_BILLING_YEAR_START, DEFAULT_BILLING_YEAR_START),
            ): TextSelector(),
            vol.Optional(
                CONF_SCAN_INTERVAL_HOURS,
                default=current.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL_HOURS,
                    max=MAX_SCAN_INTERVAL_HOURS,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def clean_tariff(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Drop empty fields, so an unset price stays absent rather than becoming 0."""
    return {key: value for key, value in user_input.items() if value is not None}


class PlusPortalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user through connecting one PlusPortal account."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold the verified credentials until the tariff step completes."""
        self._credentials: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the tenant and credentials, and verify them against the portal."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                resolve_base_url(user_input[CONF_TENANT])
            except ValueError:
                errors[CONF_TENANT] = "invalid_tenant"
            else:
                user_id, error = await self._verify(
                    user_input[CONF_TENANT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                if error is not None:
                    errors["base"] = error
                else:
                    await self.async_set_unique_id(f"{user_input[CONF_TENANT]}-{user_id}")
                    self._abort_if_unique_id_configured()
                    self._credentials = dict(user_input)
                    return await self.async_step_tariff()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer the tariff while the user is already here.

        Every field is optional: submitting an empty form tracks consumption
        only, and the same form is available afterwards under Configure.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = clean_tariff(user_input)
            try:
                _validate_tariff(cleaned)
            except ValueError as err:
                errors[str(err)] = f"invalid_{err}"
            else:
                return self.async_create_entry(
                    title=f"PlusPortal {self._credentials[CONF_TENANT]}",
                    data=self._credentials,
                    options=cleaned,
                )

        return self.async_show_form(step_id="tariff", data_schema=tariff_schema({}), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start over when the portal stops accepting the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh password and verify it before storing."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            _, error = await self._verify(
                entry.data[CONF_TENANT],
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_SCHEMA, errors=errors
        )

    async def _verify(
        self, tenant: str, username: str, password: str
    ) -> tuple[int | None, str | None]:
        """Log in and confirm the account actually has metering points.

        An account with none would produce an integration with no entities and
        no explanation, so it is refused here rather than after setup.
        """
        try:
            async with PlusPortalClient(tenant, username, password) as client:
                session = await client.login()
                meter_points = await client.get_meter_points()
        except AuthenticationError:
            return None, "invalid_auth"
        except PortalUnavailableError:
            return None, "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while verifying PlusPortal credentials")
            return None, "unknown"

        if not meter_points:
            return None, "no_meters"
        return session.user_id, None

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> PlusPortalOptionsFlow:
        """Return the options flow for tariff configuration."""
        return PlusPortalOptionsFlow()


class PlusPortalOptionsFlow(OptionsFlow):
    """Let the user set the tariff the portal does not provide."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect tariff prices and the polling interval."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = clean_tariff(user_input)
            try:
                _validate_tariff(cleaned)
            except ValueError as err:
                errors[str(err)] = f"invalid_{err}"
            else:
                return self.async_create_entry(data=cleaned)

        return self.async_show_form(step_id="init", data_schema=self._schema(), errors=errors)

    def _schema(self) -> vol.Schema:
        """Build the options schema, pre-filled with the current settings."""
        return tariff_schema(self.config_entry.options)


def _validate_tariff(options: Mapping[str, Any]) -> None:
    """Reject option combinations the cost model cannot use.

    Raises ``ValueError`` carrying the offending option key, so the caller can
    attach the error to the right form field.
    """
    raw_start = options.get(CONF_BILLING_YEAR_START, DEFAULT_BILLING_YEAR_START)
    try:
        billing_year_start = parse_billing_year_start(raw_start)
    except ValueError:
        raise ValueError(CONF_BILLING_YEAR_START) from None

    if CONF_ENERGY_PRICE not in options:
        return

    try:
        Tariff(
            energy_price_ct_per_kwh=_decimal(options[CONF_ENERGY_PRICE]),
            base_price_eur_per_year=_decimal(options.get(CONF_BASE_PRICE, 0)),
            monthly_advance_eur=(
                _decimal(options[CONF_MONTHLY_ADVANCE]) if CONF_MONTHLY_ADVANCE in options else None
            ),
            billing_year_start=billing_year_start,
        )
    except ValueError:
        raise ValueError(CONF_ENERGY_PRICE) from None


def _decimal(value: Any) -> Any:
    """Convert a form value into the Decimal the cost model expects."""
    from decimal import Decimal

    return Decimal(str(value))
