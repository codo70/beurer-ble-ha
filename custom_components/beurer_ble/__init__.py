"""The Beurer BLE integration (scales and blood pressure monitors)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_FAMILY, CONF_MODEL
from .coordinator import BeurerCoordinator
from .devices import DeviceModel, identify

PLATFORMS = [Platform.SENSOR]

type BeurerConfigEntry = ConfigEntry[BeurerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BeurerConfigEntry) -> bool:
    """Set up a device from a config entry."""
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
