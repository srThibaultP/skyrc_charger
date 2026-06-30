from __future__ import annotations

from datetime import timedelta
import logging

from bleak import BleakScanner

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CHANNELS_BY_MODEL, DOMAIN, MODEL_MC3000, MODEL_MC5000
from .mc3000_client import BLE_NAMES as MC3000_BLE_NAMES, Mc3000Client
from .mc5000_client import BLE_NAMES as MC5000_BLE_NAMES, Mc5000Client
from .models import ChargerClient

_LOGGER = logging.getLogger(__name__)

SCAN_TIMEOUT = 30.0
UPDATE_INTERVAL = timedelta(seconds=10)

CLIENT_BY_MODEL = {
    MODEL_MC3000: Mc3000Client,
    MODEL_MC5000: Mc5000Client,
}

BLE_NAMES_BY_MODEL = {
    MODEL_MC3000: MC3000_BLE_NAMES,
    MODEL_MC5000: MC5000_BLE_NAMES,
}


class SkyrcChargerCoordinator(DataUpdateCoordinator):
    """Coordinator that polls either an MC3000 or MC5000 over BLE."""

    def __init__(self, hass: HomeAssistant, address: str, model: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.address = address
        self.model = model
        self.channel_count = CHANNELS_BY_MODEL[model]

        self.charger: ChargerClient | None = None
        self._last_device = None
        self._last_good_data = None
        self.pause_polling = False

    async def _find_device(self):
        names = BLE_NAMES_BY_MODEL[self.model]
        _LOGGER.info("SkyRC %s: scanning for BLE device %s", self.model, self.address)

        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)

        fallback = None
        for device in devices:
            name = device.name or ""
            address = device.address or ""

            if address.upper() == self.address.upper():
                return device

            if name in names:
                fallback = device

        if fallback is not None:
            _LOGGER.warning(
                "SkyRC %s: target address not found, using fallback device %s",
                self.model,
                fallback.address,
            )
            return fallback

        return None

    async def _ensure_charger(self) -> ChargerClient:
        if self.pause_polling:
            raise UpdateFailed(f"SkyRC {self.model} polling paused for companion app mode")

        if self.charger is not None:
            return self.charger

        device = self._last_device or await self._find_device()
        if device is None:
            raise UpdateFailed(f"SkyRC {self.model} not found at {self.address}")

        self._last_device = device
        client_cls = CLIENT_BY_MODEL[self.model]
        self.charger = client_cls(device)
        return self.charger

    async def async_enable_companion_app_mode(self) -> None:
        _LOGGER.info("SkyRC %s: enabling companion app mode", self.model)
        self.pause_polling = True

        if self.charger is not None:
            try:
                if self.charger.is_connected:
                    await self.charger.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("SkyRC %s: disconnect failed: %r", self.model, err)

        self.charger = None
        self.async_update_listeners()

    async def async_disable_companion_app_mode(self) -> None:
        _LOGGER.info("SkyRC %s: disabling companion app mode", self.model)
        self.pause_polling = False
        self.charger = None
        await self.async_request_refresh()

    def _build_data(self, state):
        return {"state": state, "device": state.device, "channels": state.channels}

    async def _async_update_data(self):
        if self.pause_polling:
            if self._last_good_data is not None:
                return self._last_good_data
            raise UpdateFailed(f"SkyRC {self.model} polling paused for companion app mode")

        try:
            charger = await self._ensure_charger()

            if not charger.is_connected:
                await charger.connect()

            state = await charger.async_update()
            data = self._build_data(state)
            self._last_good_data = data
            return data

        except Exception as err:  # noqa: BLE001
            self.charger = None

            if self._last_good_data is not None:
                _LOGGER.warning(
                    "SkyRC %s: update failed, keeping last known data: %r",
                    self.model,
                    err,
                )
                return self._last_good_data

            raise UpdateFailed(f"Error communicating with SkyRC {self.model}: {err!r}") from err
