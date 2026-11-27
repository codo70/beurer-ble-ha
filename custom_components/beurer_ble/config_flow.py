"""Config flow for the Beurer BLE integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import (
    CONF_AUTO_ASSIGN,
    CONF_CONSENT,
    CONF_FAMILY,
    CONF_MODEL,
    CONF_PERSONS,
    DOMAIN,
)
from .devices import FAMILY_BPM, FAMILY_STANDARD, DeviceModel, identify


def _model_of(info: BluetoothServiceInfoBleak) -> DeviceModel | None:
    return identify(info.name, info.service_uuids)


class BeurerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Beurer devices."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._devices: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options: person mapping, guest assignment, consent codes."""
        return BeurerOptionsFlow()

    def _create(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        model = _model_of(info)
        assert model is not None
        name = info.name or model.model
        return self.async_create_entry(
            title=f"Beurer {model.model}" if not name.lower().startswith(("beurer", "sanitas")) else name,
            data={
                CONF_ADDRESS: info.address,
                CONF_NAME: name,
                CONF_FAMILY: model.family,
                CONF_MODEL: model.model,
            },
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovered device."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if _model_of(discovery_info) is None:
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device."""
        assert self._discovery is not None
        if user_input is not None:
            return self._create(self._discovery)
        model = _model_of(self._discovery)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery.name,
                "model": model.model if model else "?",
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a device from the Bluetooth devices seen."""
        if user_input is not None:
            info = self._devices[user_input[CONF_ADDRESS]]
            await self.async_set_unique_id(info.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self._create(info)

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address not in configured and _model_of(info) is not None:
                self._devices[info.address] = info
        if not self._devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            a: f"{i.name} – {_model_of(i).model} ({a})"
                            for a, i in sorted(self._devices.items(), key=lambda x: x[1].name)
                        }
                    )
                }
            ),
        )


class BeurerOptionsFlow(OptionsFlow):
    """Map device persons to HA persons; consent codes; guest auto-assign."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form."""
        entry = self.config_entry
        family = entry.data.get(CONF_FAMILY)
        coordinator = getattr(entry, "runtime_data", None)
        persons: dict[str, str] = dict(coordinator.persons) if coordinator else {}
        current: dict[str, str] = entry.options.get(CONF_PERSONS, {})
        # Form field label -> device person id
        fields = {f"{label} [{pid}]": pid for pid, label in sorted(persons.items()) if pid != "guest"}

        if user_input is not None:
            data: dict[str, Any] = {
                CONF_PERSONS: {
                    pid: user_input[field] for field, pid in fields.items() if user_input.get(field)
                }
            }
            if family == FAMILY_STANDARD:
                data[CONF_CONSENT] = user_input.get(CONF_CONSENT, "")
            if family != FAMILY_BPM:
                data[CONF_AUTO_ASSIGN] = user_input.get(CONF_AUTO_ASSIGN, True)
            return self.async_create_entry(data=data)

        schema: dict[Any, Any] = {}
        for field, pid in fields.items():
            schema[
                vol.Optional(field, description={"suggested_value": current.get(pid)})
            ] = EntitySelector(EntitySelectorConfig(domain="person"))
        if family != FAMILY_BPM:
            schema[
                vol.Optional(CONF_AUTO_ASSIGN, default=entry.options.get(CONF_AUTO_ASSIGN, True))
            ] = bool
        if family == FAMILY_STANDARD:
            schema[
                vol.Optional(CONF_CONSENT, default=entry.options.get(CONF_CONSENT, ""))
            ] = str
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "persons": ", ".join(fields) if fields else "–",
            },
        )
