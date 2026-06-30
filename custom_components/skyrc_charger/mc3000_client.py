"""MC3000 driver.

Thin adapter around the third-party `skyrc-ble` library (the same one used
by jperquin/ha-skyrc-mc3000) so it conforms to our common ChargerClient
interface.
"""

from __future__ import annotations

import logging

from .const import MC3000_BLE_NAME_PATTERNS
from .models import ChannelData, ChargerClient, ChargerState, DeviceData

_LOGGER = logging.getLogger(__name__)

BLE_NAME_PATTERNS = MC3000_BLE_NAME_PATTERNS


def _enum_name(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", str(value)).lower()


class Mc3000Client(ChargerClient):
    channel_count = 8

    def __init__(self, ble_device) -> None:
        from skyrc_ble import Mc3000

        self._device = ble_device
        self._charger = Mc3000(ble_device)

    @property
    def is_connected(self) -> bool:
        return bool(self._charger.is_connected)

    async def connect(self) -> None:
        await self._charger.connect()

    async def disconnect(self) -> None:
        await self._charger.disconnect()

    async def async_update(self) -> ChargerState:
        await self._charger.update()
        state = self._charger.state

        basic = state.basic_data
        device = DeviceData(
            name=self._charger.name or "SkyRC MC3000",
            address=self._charger.address,
            manufacturer=self._charger.manufacturer or "SkyRC",
            model=self._charger.model or "MC3000",
            hw_version=str(self._charger.hw_version) if self._charger.hw_version else None,
            sw_version=str(self._charger.sw_version) if self._charger.sw_version else None,
            input_voltage=getattr(basic, "input_voltage", None),
        )

        channels = []
        for idx, ch in enumerate(state.channels):
            channels.append(
                ChannelData(
                    index=idx,
                    status=_enum_name(getattr(ch, "status", None)),
                    chemistry=_enum_name(getattr(ch, "type", None)),
                    mode=_enum_name(getattr(ch, "mode", None)),
                    voltage=getattr(ch, "voltage", None),
                    current=getattr(ch, "current", None),
                    capacity=getattr(ch, "capacity", None),
                    temperature=getattr(ch, "temperature", None),
                    resistance=getattr(ch, "resistance", None),
                    time=getattr(ch, "time", None),
                )
            )

        return ChargerState(device=device, channels=channels)

    async def start_channel(self, channel: int, **kwargs) -> None:
        """Start the program already configured on the device for this slot."""
        await self._charger.start_charge(channel)

    async def stop_channel(self, channel: int) -> None:
        await self._charger.stop_charge(channel)

    async def stop_all(self) -> None:
        for channel in range(self.channel_count):
            await self._charger.stop_charge(channel)
