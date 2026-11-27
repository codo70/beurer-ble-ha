"""The Beurer BF700 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant

from .coordinator import BF700Coordinator

PLATFORMS = [Platform.SENSOR]

type BF700ConfigEntry = ConfigEntry[BF700Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BF700ConfigEntry) -> bool:
    """Set up a scale from a config entry."""
    coordinator = BF700Coordinator(
        hass, entry, entry.data[CONF_ADDRESS], entry.data.get(CONF_NAME, "Beurer BF700")
    )
    await coordinator.async_start()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BF700ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
