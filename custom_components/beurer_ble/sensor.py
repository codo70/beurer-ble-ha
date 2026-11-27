"""Sensors for the Beurer BLE integration."""

from __future__ import annotations

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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BeurerConfigEntry
from .const import DOMAIN, SIGNAL_UPDATE
from .devices import FAMILY_BPM
from .coordinator import BeurerCoordinator


@dataclass(frozen=True, kw_only=True)
class BeurerSensorDescription(SensorEntityDescription):
    """Describes a per-person measurement sensor; key = measurement dict key."""

    attr_keys: tuple[str, ...] = ()


_PCT = {"state_class": SensorStateClass.MEASUREMENT, "native_unit_of_measurement": PERCENTAGE,
        "suggested_display_precision": 1}
_KG = {"device_class": SensorDeviceClass.WEIGHT, "state_class": SensorStateClass.MEASUREMENT,
       "native_unit_of_measurement": UnitOfMass.KILOGRAMS, "suggested_display_precision": 1}
_MMHG = {"state_class": SensorStateClass.MEASUREMENT, "native_unit_of_measurement": "mmHg"}

SENSORS: dict[str, BeurerSensorDescription] = {
    d.key: d
    for d in (
        BeurerSensorDescription(key="weight_kg", name="Gewicht", **_KG),
        BeurerSensorDescription(key="fat_pct", name="Körperfett", icon="mdi:water-percent", **_PCT),
        BeurerSensorDescription(key="water_pct", name="Körperwasser", icon="mdi:water", **_PCT),
        BeurerSensorDescription(key="muscle_pct", name="Muskelanteil", icon="mdi:arm-flex", **_PCT),
        BeurerSensorDescription(key="bone_kg", name="Knochenmasse", icon="mdi:bone", **_KG),
        BeurerSensorDescription(key="muscle_mass_kg", name="Muskelmasse", icon="mdi:arm-flex", **_KG),
        BeurerSensorDescription(key="fat_free_mass_kg", name="Fettfreie Masse", **_KG),
        BeurerSensorDescription(
            key="bmi", name="BMI", icon="mdi:human", state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        BeurerSensorDescription(
            key="bmr_kcal", name="Grundumsatz", icon="mdi:fire",
            state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement="kcal",
        ),
        BeurerSensorDescription(
            key="amr_kcal", name="Gesamtumsatz", icon="mdi:fire",
            state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement="kcal",
        ),
        BeurerSensorDescription(
            key="impedance", name="Impedanz", icon="mdi:omega",
            state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement="Ω",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        BeurerSensorDescription(
            key="systolic", name="Blutdruck systolisch", icon="mdi:heart-pulse", **_MMHG,
            attr_keys=("irregular_pulse", "body_movement", "cuff_fit_ok", "status_raw", "device_time_ok"),
        ),
        BeurerSensorDescription(key="diastolic", name="Blutdruck diastolisch", icon="mdi:heart-pulse", **_MMHG),
        BeurerSensorDescription(
            key="mean_arterial", name="Blutdruck Mitteldruck", icon="mdi:heart-pulse",
            entity_registry_enabled_default=False, **_MMHG,
        ),
        BeurerSensorDescription(
            key="pulse", name="Puls", icon="mdi:heart", state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement="bpm",
        ),
        BeurerSensorDescription(
            key="timestamp", name="Letzte Messung", device_class=SensorDeviceClass.TIMESTAMP,
        ),
    )
}


async def async_setup_entry(
    hass: HomeAssistant, entry: BeurerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors; per-person sensors appear once that person has a value."""
    coordinator = entry.runtime_data
    added: set[tuple[str, str]] = set()

    # Drop entities left over from an earlier person mapping.
    expected = {f"{coordinator.address}_battery"} | {
        person_unique_id(coordinator, pid, key)
        for pid, meas in coordinator.latest.items()
        for key in meas
        if key in SENSORS and meas[key] is not None
    }
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.domain == "sensor" and reg.unique_id not in expected:
            registry.async_remove(reg.entity_id)

    @callback
    def _add_new() -> None:
        new = []
        for pid, meas in coordinator.latest.items():
            for key in meas:
                if key in SENSORS and meas[key] is not None and (pid, key) not in added:
                    added.add((pid, key))
                    new.append(BeurerPersonSensor(coordinator, pid, SENSORS[key]))
        if new:
            async_add_entities(new)

    async_add_entities([BeurerBatterySensor(coordinator)])
    _add_new()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_UPDATE.format(entry.entry_id), _add_new)
    )


def person_unique_id(coordinator: BeurerCoordinator, pid: str, key: str) -> str:
    """Unique id; includes the HA person so a new mapping gives fresh entities."""
    base = f"{coordinator.address}_{pid}_{key}"
    if person := coordinator.ha_person(pid):
        return f"{base}_{person}"
    return base


class _BeurerEntity(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: BeurerCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=coordinator.name,
            manufacturer="Beurer",
            model=coordinator.model.model,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(self.coordinator.entry.entry_id),
                self.async_write_ha_state,
            )
        )


class BeurerBatterySensor(_BeurerEntity):
    """Battery level reported by the device."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Batterie"

    def __init__(self, coordinator: BeurerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_battery"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.battery


class BeurerPersonSensor(_BeurerEntity):
    """One value of the latest measurement of a person."""

    entity_description: BeurerSensorDescription

    def __init__(
        self, coordinator: BeurerCoordinator, pid: str, description: BeurerSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._pid = pid
        name = description.name
        if description.key == "timestamp":
            name = "Letzte Blutdruckmessung" if coordinator.model.family == FAMILY_BPM else "Letzte Wiegung"
        self._attr_unique_id = person_unique_id(coordinator, pid, description.key)
        if person := coordinator.ha_person(pid):
            # One device per HA person, shared by all Beurer devices mapped to it.
            self._attr_name = name
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"person_{person}")},
                name=coordinator.ha_person_name(person),
                manufacturer="Beurer",
                model="Person",
            )
        else:
            self._attr_name = f"{coordinator.persons.get(pid, pid)} {name}"

    @property
    def native_value(self) -> Any:
        meas = self.coordinator.latest.get(self._pid)
        if not meas:
            return None
        value = meas.get(self.entity_description.key)
        if self.entity_description.key == "timestamp" and value:
            return dt_util.utc_from_timestamp(value)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        meas = self.coordinator.latest.get(self._pid) or {}
        attrs = {k: meas[k] for k in self.entity_description.attr_keys if k in meas}
        if self.entity_description.key in ("weight_kg", "systolic"):
            attrs["device_person"] = self.coordinator.persons.get(self._pid, self._pid)
            attrs["source"] = self.coordinator.name
            if person := self.coordinator.ha_person(self._pid):
                attrs["ha_person"] = person
        return attrs or None
