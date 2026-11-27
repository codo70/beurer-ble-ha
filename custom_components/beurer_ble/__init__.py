# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 codo70. Scale protocols based on openScale (C) olie.xdev, GPL-3.0-or-later.
"""The Beurer BLE integration (scales and blood pressure monitors)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import CONF_FAMILY, CONF_MODEL, DOMAIN
from .coordinator import BeurerCoordinator
from .devices import FAMILY_SANITAS, DeviceModel, identify
from .frontend import async_setup_frontend
from .sanitas_protocol import NewUser

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type BeurerConfigEntry = ConfigEntry[BeurerCoordinator]

SERVICE_CREATE_USER = "create_scale_user"
CREATE_USER_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry"): cv.string,
        vol.Required("initials"): vol.All(cv.string, vol.Match(r"^[A-Za-z0-9]{1,3}$")),
        vol.Required("birthday"): cv.date,
        vol.Required("height"): vol.All(vol.Coerce(int), vol.Range(min=80, max=230)),
        vol.Required("gender"): vol.In(["male", "female"]),
        vol.Optional("activity", default="3"): vol.All(vol.Coerce(int), vol.Range(min=1, max=5)),
        vol.Optional("person"): cv.entity_id,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""

    async def _create_user(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry"])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("Beurer-Gerät nicht gefunden oder nicht geladen")
        coordinator: BeurerCoordinator = entry.runtime_data
        if coordinator.model.family != FAMILY_SANITAS:
            raise ServiceValidationError(
                "Benutzer anlegen geht bisher nur bei BF700/BF710/BF800/SBF70-Waagen"
            )
        birthday = call.data["birthday"]
        coordinator.pending_user = NewUser(
            initials=call.data["initials"].upper(),
            birth_year=birthday.year,
            birth_month=birthday.month,
            birth_day=birthday.day,
            height_cm=call.data["height"],
            male=call.data["gender"] == "male",
            activity=call.data["activity"],
        )
        coordinator.pending_person = call.data.get("person")

    hass.services.async_register(DOMAIN, SERVICE_CREATE_USER, _create_user, CREATE_USER_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BeurerConfigEntry) -> bool:
    """Set up a device from a config entry."""
    await async_setup_frontend(hass)
    name = entry.data.get(CONF_NAME, "Beurer")
    model = identify(name) or DeviceModel(
        entry.data.get(CONF_FAMILY, "bpm"), entry.data.get(CONF_MODEL, name)
    )
    coordinator = BeurerCoordinator(hass, entry, entry.data[CONF_ADDRESS], name, model)
    await coordinator.async_start()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: BeurerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BeurerConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
