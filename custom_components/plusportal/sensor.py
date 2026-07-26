"""Sensors summarising one PlusPortal metering point.

The Energy dashboard is fed by long-term statistics, not by these entities —
portal values are backdated, and a `total_increasing` sensor would book them
at the moment of import. What lives here is the at-a-glance state: recent
consumption, how fresh and how final the data is, and what the bill is heading
towards.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from pyplusportal.cost import Tariff

from .const import DOMAIN
from .coordinator import MeterData, PlusPortalConfigEntry, PlusPortalCoordinator

_LOGGER = logging.getLogger(__name__)

CURRENCY_EUR = "EUR"


def _last_complete_day(data: MeterData) -> tuple[date, Decimal] | None:
    """Energy of the most recent day that has billable readings."""
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    for reading in data.readings:
        if reading.billable:
            by_day[reading.day] += reading.value
    if not by_day:
        return None
    day = max(by_day)
    return day, by_day[day]


def _last_day_value(data: MeterData) -> Decimal | None:
    result = _last_complete_day(data)
    return None if result is None else result[1]


def _last_day_attributes(data: MeterData) -> Mapping[str, Any]:
    result = _last_complete_day(data)
    return {} if result is None else {"date": result[0].isoformat()}


def _this_month(data: MeterData) -> Decimal | None:
    return data.overview.this_month_sum if data.overview else None


def _previous_month(data: MeterData) -> Decimal | None:
    return data.overview.prev_month_sum if data.overview else None


def _last_measurement(data: MeterData) -> datetime | None:
    return data.overview.last_value_at if data.overview else None


def _data_quality(data: MeterData) -> Decimal | None:
    """Share of fetched readings that are final or billable, in percent."""
    if not data.readings:
        return None
    billable = sum(1 for reading in data.readings if reading.billable)
    return Decimal(billable) / Decimal(len(data.readings)) * 100


def _observed_cost(data: MeterData, tariff: Tariff) -> Decimal | None:
    return data.projection.observed.total_eur if data.projection else None


def _energy_cost(data: MeterData, tariff: Tariff) -> Decimal | None:
    """Arbeitspreis component only."""
    return data.projection.observed.energy_eur if data.projection else None


def _standing_charge(data: MeterData, tariff: Tariff) -> Decimal | None:
    """Grundpreis accrued so far, charged pro rata over the billing year."""
    return data.projection.observed.base_eur if data.projection else None


def _energy_price(data: MeterData, tariff: Tariff) -> Decimal:
    """Price per kWh, so the Energy dashboard can attach it to a source."""
    return tariff.energy_price_eur_per_kwh


def _base_price(data: MeterData, tariff: Tariff) -> Decimal:
    """Return the configured annual standing charge, unapportioned."""
    return tariff.base_price_eur_per_year


def _billing_year_attributes(data: MeterData, tariff: Tariff) -> Mapping[str, Any]:
    """Name the period an amount refers to, so it can be checked."""
    if data.projection is None:
        return {}
    start, end = data.projection.billing_year
    return {
        "billing_year_start": start.isoformat(),
        "billing_year_end": end.isoformat(),
        "data_coverage_percent": float(round(data.projection.coverage * 100, 1)),
    }


def _projected_cost(data: MeterData, tariff: Tariff) -> Decimal | None:
    return data.projection.projected_eur if data.projection else None


def _expected_settlement(data: MeterData, tariff: Tariff) -> Decimal | None:
    return data.projection.settlement_eur if data.projection else None


def _settlement_attributes(data: MeterData, tariff: Tariff) -> Mapping[str, Any]:
    """Report what the settlement is measured against; alone it means nothing."""
    if data.projection is None:
        return {}
    projection = data.projection
    start, end = projection.billing_year
    # Attributes are serialised to JSON, which cannot carry Decimal: the state
    # is dropped and the entity goes unavailable with only a recorder warning.
    # These are already rounded to the cent, so float loses nothing that shows.
    return {
        "advances_paid_eur": _as_number(projection.advances_paid_eur),
        "advances_due_eur": _as_number(projection.advances_due_eur),
        "billing_year_start": start.isoformat(),
        "billing_year_end": end.isoformat(),
        "data_coverage_percent": float(round(projection.coverage * 100, 1)),
    }


def _as_number(value: Decimal | None) -> float | None:
    """Convert an amount for an attribute, keeping ``None`` distinct from zero."""
    return None if value is None else float(value)


@dataclass(frozen=True, kw_only=True)
class PlusPortalSensorDescription(SensorEntityDescription):
    """Describes a PlusPortal sensor and how to read its value."""

    value_fn: Callable[[MeterData], Any] | None = None
    tariff_value_fn: Callable[[MeterData, Tariff], Any] | None = None
    attributes_fn: Callable[[MeterData], Mapping[str, Any]] | None = None
    tariff_attributes_fn: Callable[[MeterData, Tariff], Mapping[str, Any]] | None = None


CONSUMPTION_SENSORS: tuple[PlusPortalSensorDescription, ...] = (
    PlusPortalSensorDescription(
        key="last_day",
        translation_key="last_day",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=_last_day_value,
        attributes_fn=_last_day_attributes,
    ),
    PlusPortalSensorDescription(
        key="this_month",
        translation_key="this_month",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=_this_month,
    ),
    PlusPortalSensorDescription(
        key="previous_month",
        translation_key="previous_month",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=_previous_month,
    ),
    PlusPortalSensorDescription(
        key="last_measurement",
        translation_key="last_measurement",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_measurement,
    ),
    PlusPortalSensorDescription(
        key="data_quality",
        translation_key="data_quality",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=_data_quality,
    ),
)

COST_SENSORS: tuple[PlusPortalSensorDescription, ...] = (
    PlusPortalSensorDescription(
        key="energy_price",
        translation_key="energy_price",
        native_unit_of_measurement="EUR/kWh",
        suggested_display_precision=4,
        entity_category=EntityCategory.DIAGNOSTIC,
        tariff_value_fn=_energy_price,
    ),
    PlusPortalSensorDescription(
        key="standing_charge_per_year",
        translation_key="standing_charge_per_year",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        tariff_value_fn=_base_price,
    ),
    PlusPortalSensorDescription(
        key="energy_cost_to_date",
        translation_key="energy_cost_to_date",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        tariff_value_fn=_energy_cost,
        tariff_attributes_fn=_billing_year_attributes,
    ),
    PlusPortalSensorDescription(
        key="standing_charge_to_date",
        translation_key="standing_charge_to_date",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        tariff_value_fn=_standing_charge,
        tariff_attributes_fn=_billing_year_attributes,
    ),
    PlusPortalSensorDescription(
        key="cost_to_date",
        translation_key="cost_to_date",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        tariff_value_fn=_observed_cost,
        tariff_attributes_fn=_billing_year_attributes,
    ),
    PlusPortalSensorDescription(
        key="projected_cost_billing_year",
        translation_key="projected_cost_billing_year",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        tariff_value_fn=_projected_cost,
        tariff_attributes_fn=_billing_year_attributes,
    ),
    PlusPortalSensorDescription(
        key="expected_settlement_billing_year",
        translation_key="expected_settlement_billing_year",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        tariff_value_fn=_expected_settlement,
        tariff_attributes_fn=_settlement_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlusPortalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one set of sensors per metering point."""
    coordinator = entry.runtime_data
    descriptions = CONSUMPTION_SENSORS
    if coordinator.tariff is not None:
        descriptions += COST_SENSORS

    async_add_entities(
        PlusPortalSensor(coordinator, meter_point_id, description)
        for meter_point_id in coordinator.data
        for description in descriptions
    )


class PlusPortalSensor(CoordinatorEntity[PlusPortalCoordinator], SensorEntity):
    """A single figure about one metering point."""

    entity_description: PlusPortalSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PlusPortalCoordinator,
        meter_point_id: int,
        description: PlusPortalSensorDescription,
    ) -> None:
        """Bind the sensor to one metering point."""
        super().__init__(coordinator)
        self.entity_description = description
        self._meter_point_id = meter_point_id

        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{meter_point_id}_{description.key}"

        meter_point = coordinator.data[meter_point_id].meter_point
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.unique_id}_{meter_point_id}")},
            name=meter_point.name or str(meter_point_id),
            # Where the data comes from, not who built the meter. Naming the
            # portal operator here would read as "by Thüga SmartService", which
            # is exactly the affiliation this project does not have.
            manufacturer="PlusPortal",
            model=meter_point.primary_taf.title if meter_point.primary_taf else None,
            serial_number=meter_point.name or None,
        )

    @property
    def suggested_object_id(self) -> str:
        """Build the entity id from the description key, not the display name.

        Home Assistant derives entity ids from the translated name, so a German
        instance produced sensor.…_energiekosten_seit_beginn_des_abrechnungs-
        jahres. Entity ids are referenced from templates and automations and
        should not depend on the interface language, so the English key is used
        and the translation is left to do only what it is for.
        """
        # Home Assistant prefixes the device name itself.
        return self.entity_description.key

    @property
    def _data(self) -> MeterData | None:
        return self.coordinator.data.get(self._meter_point_id)

    @property
    def native_value(self) -> Any:
        """Current value, or ``None`` so the state shows as unknown.

        Reporting 0 for absent data would be indistinguishable from a real
        reading of zero consumption.
        """
        data = self._data
        if data is None:
            return None

        description = self.entity_description
        if description.tariff_value_fn is not None:
            tariff = self.coordinator.tariff
            return None if tariff is None else description.tariff_value_fn(data, tariff)
        if description.value_fn is not None:
            return description.value_fn(data)
        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Extra context, such as which day a value belongs to."""
        data = self._data
        if data is None:
            return None

        description = self.entity_description
        if description.tariff_attributes_fn is not None:
            tariff = self.coordinator.tariff
            return None if tariff is None else description.tariff_attributes_fn(data, tariff)
        if description.attributes_fn is not None:
            return description.attributes_fn(data)
        return None
