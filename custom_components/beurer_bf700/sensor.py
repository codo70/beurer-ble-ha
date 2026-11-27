"""Sensors for the Beurer BF700 integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BF700ConfigEntry
from .const import DOMAIN, SIGNAL_NEW_USER, SIGNAL_UPDATE
from .coordinator import BF700Coordinator
from .protocol import Measurement


@dataclass(frozen=True, kw_only=True)
class BF700SensorDescription(SensorEntityDescription):
    """Describes a per-user measurement sensor."""

    value_fn: Callable[[Measurement], Any]


USER_SENSORS: tuple[BF700SensorDescription, ...] = (
    BF700SensorDescription(
        key="weight",
        name="Gewicht",
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        value_fn=lambda m: m.weight_kg,
    ),
    BF700SensorDescription(
        key="fat",
        name="Körperfett",
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda m: m.fat_pct,
    ),
    BF700SensorDescription(
        key="water",
        name="Körperwasser",
        icon="mdi:water",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda m: m.water_pct,
    ),
    BF700SensorDescription(
        key="muscle",
        name="Muskelanteil",
        icon="mdi:arm-flex",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda m: m.muscle_pct,
    ),
    BF700SensorDescription(
        key="bone",
        name="Knochenmasse",
        icon="mdi:bone",
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        value_fn=lambda m: m.bone_kg,
    ),
    BF700SensorDescription(
        key="impedance",
        name="Impedanz",
        icon="mdi:omega",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="Ω",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.impedance,
    ),
    BF700SensorDescription(
        key="last_measurement",
        name="Letzte Messung",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: dt_util.utc_from_timestamp(m.timestamp),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BF700ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    added: set[int] = set()

    @callback
    def _add_user(uid: int) -> None:
        if uid in added:
            return
        added.add(uid)
        async_add_entities(BF700UserSensor(coordinator, uid, d) for d in USER_SENSORS)

    async_add_entities([BF700BatterySensor(coordinator)])
    for uid in list(coordinator.users):
        _add_user(uid)
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_USER.format(entry.entry_id), _add_user)
    )


class _BF700Entity(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: BF700Coordinator) -> None:
        self.coordinator = coordinator
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=coordinator.name,
            manufacturer="Beurer",
            model="BF700",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(self.coordinator.entry.entry_id),
                self.async_write_ha_state,
            )
        )


class BF700BatterySensor(_BF700Entity):
    """Battery level reported by the scale."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Batterie"

    def __init__(self, coordinator: BF700Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_battery"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.battery


class BF700UserSensor(_BF700Entity):
    """One value of the latest measurement of a scale user."""

    entity_description: BF700SensorDescription

    def __init__(
        self, coordinator: BF700Coordinator, uid: int, description: BF700SensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._uid = uid
        user = coordinator.users.get(uid)
        initials = user.initials if user else str(uid)
        self._attr_name = f"{initials} {description.name}"
        self._attr_unique_id = f"{coordinator.address}_{uid}_{description.key}"

    @property
    def native_value(self) -> Any:
        meas = self.coordinator.latest.get(self._uid)
        return self.entity_description.value_fn(meas) if meas else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "weight":
            return None
        user = self.coordinator.users.get(self._uid)
        return {"scale_user_id": self._uid, "birth_year": user.birth_year if user else None}
