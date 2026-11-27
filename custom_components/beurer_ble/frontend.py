# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 codo70. Scale protocols based on openScale (C) olie.xdev, GPL-3.0-or-later.
"""Lovelace card for the Beurer BLE integration and its websocket API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_integration

from .const import DOMAIN

STATIC_URL = f"/{DOMAIN}_static"
SCRIPT = "beurer-ble-card.js"
_DATA = f"{DOMAIN}_frontend"


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve the card script and load it on every dashboard (once)."""
    data = hass.data.setdefault(_DATA, {})
    if data.get("static"):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), False)]
    )
    integration = await async_get_integration(hass, DOMAIN)
    # Version in the URL so browsers load the new script after an update.
    frontend.add_extra_js_url(hass, f"{STATIC_URL}/{SCRIPT}?v={integration.version}")
    websocket_api.async_register_command(hass, ws_persons)
    data["static"] = True


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/persons"})
@callback
def ws_persons(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """List the persons with their sensor entity ids by value key."""
    # Imported here to avoid an import cycle with sensor.py -> __init__.py.
    from .sensor import SENSORS, person_unique_id

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    persons: dict[str, dict[str, Any]] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        coordinator = entry.runtime_data
        for pid, meas in coordinator.latest.items():
            for key in meas:
                if key not in SENSORS:
                    continue
                entity_id = ent_reg.async_get_entity_id(
                    "sensor", DOMAIN, person_unique_id(coordinator, pid, key)
                )
                reg = ent_reg.async_get(entity_id) if entity_id else None
                if reg is None or reg.device_id is None:
                    continue
                ha_person = coordinator.ha_person(pid)
                # Mapped persons share one device across scale and monitor.
                group = ha_person or ("guest" if pid == "guest" else f"{entry.entry_id}:{pid}")
                person = persons.get(group)
                if person is None:
                    name = coordinator.persons.get(pid, pid)
                    if ha_person:
                        device = dev_reg.async_get(reg.device_id)
                        name = (device and (device.name_by_user or device.name)) or name
                    person = persons[group] = {
                        "id": group,
                        "name": name,
                        "ha_person": ha_person,
                        "entities": {},
                    }
                person["entities"].setdefault(key, entity_id)
    connection.send_result(msg["id"], sorted(persons.values(), key=lambda p: p["name"]))
