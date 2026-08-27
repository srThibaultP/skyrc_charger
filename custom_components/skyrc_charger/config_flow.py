from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.data_entry_flow import FlowResult

from .const import (
    BLE_SERVICE_UUID,
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_NAME,
    DOMAIN,
    MODEL_MC3000,
    MODEL_MC5000,
    MODELS,
    MODEL_NAMES,
)
from .coordinator import BLE_NAME_PATTERNS_BY_MODEL, _name_matches

_LOGGER = logging.getLogger(__name__)


class SkyrcChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SkyRC MC3000 / MC5000."""

    VERSION = 1

    def __init__(self) -> None:
        self._model: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1: pick the charger model."""
        if user_input is not None:
            self._model = user_input[CONF_MODEL]
            return await self.async_step_device()

        schema = vol.Schema(
            {vol.Required(CONF_MODEL, default=MODEL_MC3000): vol.In(MODELS)}
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 2: pick/enter the BLE device address."""
        errors: dict[str, str] = {}
        model = self._model or MODEL_MC3000

        if user_input is not None:
            address = str(user_input[CONF_ADDRESS]).strip()
            name = str(user_input.get(CONF_NAME) or MODEL_NAMES[model]).strip()

            await self.async_set_unique_id(f"{model}-{address.upper()}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={CONF_ADDRESS: address, CONF_NAME: name, CONF_MODEL: model},
            )

        devices = await self._async_discover_devices(model)
        if not devices:
            errors["base"] = "no_devices_found"

        return self.async_show_form(
            step_id="device",
            data_schema=self._build_schema(devices, model),
            errors=errors,
        )

    async def _async_discover_devices(self, model: str) -> dict[str, str]:
        """List candidate chargers from Home Assistant's Bluetooth manager.

        Going through HA rather than running our own BleakScanner sweep means
        devices seen by ESPHome/Shelly Bluetooth proxies show up too, and we
        don't take the adapter away from other integrations for 15 seconds
        while the form loads.
        """
        devices: dict[str, str] = {}

        try:
            infos = bluetooth.async_discovered_service_info(self.hass, connectable=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("HA Bluetooth discovery unavailable, using manual setup: %r", err)
            return devices

        patterns = BLE_NAME_PATTERNS_BY_MODEL[model]

        for info in infos:
            address = info.address or ""
            if not address:
                continue

            name = info.name or ""
            service_uuids = [uuid.lower() for uuid in (info.service_uuids or [])]

            if not (_name_matches(name, patterns) or BLE_SERVICE_UUID in service_uuids):
                continue

            label = f"{name or 'SkyRC charger'} ({address})"
            rssi = getattr(info, "rssi", None)
            if rssi is not None:
                label = f"{label} — RSSI {rssi}"

            devices[address] = label

        return devices

    def _build_schema(self, devices: dict[str, str], model: str) -> vol.Schema:
        default_name = MODEL_NAMES[model]
        if devices:
            return vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(devices),
                    vol.Optional(CONF_NAME, default=default_name): str,
                }
            )
        return vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default=default_name): str,
            }
        )
