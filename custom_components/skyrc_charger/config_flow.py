from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_NAME,
    DEFAULT_NAME,
    DOMAIN,
    MODEL_MC3000,
    MODEL_MC5000,
    MODELS,
)
from .coordinator import BLE_NAMES_BY_MODEL

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
            name = str(user_input.get(CONF_NAME) or f"{DEFAULT_NAME} ({MODELS[model]})").strip()

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
        devices: dict[str, str] = {}

        try:
            from bleak import BleakScanner
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("BLE discovery unavailable, using manual setup: %r", err)
            return devices

        try:
            discovered = await BleakScanner.discover(timeout=15.0)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("BLE discovery failed, using manual setup: %r", err)
            return devices

        names = BLE_NAMES_BY_MODEL[model]
        for device in discovered:
            name = device.name or ""
            address = device.address or ""
            if address and name in names:
                devices[address] = f"{name} ({address})"

        return devices

    def _build_schema(self, devices: dict[str, str], model: str) -> vol.Schema:
        default_name = f"{DEFAULT_NAME} ({MODELS[model]})"
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
