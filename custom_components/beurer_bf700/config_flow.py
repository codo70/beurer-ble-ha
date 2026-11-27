"""Config flow for the Beurer BF700 integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .const import DOMAIN
from .protocol import is_supported_name


class BF700ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Beurer BF700."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._devices: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovered scale."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered scale."""
        assert self._discovery is not None
        name = self._discovery.name or "Beurer BF700"
        if user_input is not None:
            return self.async_create_entry(
                title=name, data={CONF_ADDRESS: self._discovery.address, CONF_NAME: name}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders={"name": name}
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a scale from the Bluetooth devices seen."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = self._devices.get(address, "Beurer BF700")
            return self.async_create_entry(title=name, data={CONF_ADDRESS: address, CONF_NAME: name})

        configured = self._async_current_ids(include_ignore=False)
        infos = [
            i
            for i in async_discovered_service_info(self.hass, connectable=True)
            if i.address not in configured and i.name and i.name != i.address
        ]
        supported = [i for i in infos if is_supported_name(i.name)]
        # Fall back to every named device if the scale uses an unknown name.
        for info in supported or infos:
            self._devices[info.address] = info.name
        if not self._devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {a: f"{n} ({a})" for a, n in sorted(self._devices.items(), key=lambda x: x[1])}
                    )
                }
            ),
        )
