"""MC3000 driver.

Thin adapter around the third-party `skyrc-ble` library (the same one used
by jperquin/ha-skyrc-mc3000) so it conforms to our common ChargerClient
interface. The library already connects through
bleak_retry_connector.establish_connection(), so there is nothing to fix
on this side of the BLE stack; the noise it produces is handled by
logging_utils.
"""

from __future__ import annotations

import asyncio
import logging

from .const import MC3000_BLE_NAME_PATTERNS
from .models import ChannelData, ChargerClient, ChargerState, DeviceData, VoltageCurve

_LOGGER = logging.getLogger(__name__)

BLE_NAME_PATTERNS = MC3000_BLE_NAME_PATTERNS

VOLTAGE_CURVE_FRAME_SIZE = 246
VOLTAGE_CURVE_SAMPLE_BYTES = 240
VOLTAGE_CURVE_TIMEOUT = 3.0

ALL_CHANNELS_MASK = 0x0F


def _enum_name(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", str(value)).lower()


class Mc3000Client(ChargerClient):
    channel_count = 4

    supports_voltage_curves = True
    supports_programs = True

    def __init__(self, ble_device) -> None:
        from skyrc_ble import Mc3000

        self._device = ble_device
        self._charger = Mc3000(ble_device)

    @property
    def is_connected(self) -> bool:
        return bool(self._charger.is_connected)

    def set_ble_device(self, ble_device) -> None:
        """Adopt a freshly discovered BLEDevice for the next connect."""
        self._device = ble_device
        self._charger.set_ble_device(ble_device)

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
            temperature_unit=_enum_name(getattr(basic, "temp_unit", None)),
            display_mode=_enum_name(getattr(basic, "display", None)),
            cooling_fan_mode=_enum_name(getattr(basic, "cooling_fan", None)),
            system_beep=getattr(basic, "system_beep", None),
            screensaver=getattr(basic, "screensaver", None),
        )

        channels = []
        for idx, ch in enumerate(state.channels):
            if ch is None:
                channels.append(ChannelData(index=idx))
                continue

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
                    cycle_count=getattr(ch, "count", None),
                    led=_enum_name(getattr(ch, "led", None)),
                )
            )

        return ChargerState(device=device, channels=channels)

    async def start_channel(self, channel: int, **kwargs) -> None:
        """Start the program already configured on the device for this slot."""
        await self._charger.start_charge(channel)

    async def stop_channel(self, channel: int) -> None:
        await self._charger.stop_charge(channel)

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs) -> None:
        # The MC3000 replays whatever program each slot already holds, so
        # there is nothing per-slot to send here.
        if hasattr(self._charger, "start_charge_multi"):
            await self._charger.start_charge_multi(ALL_CHANNELS_MASK)
            return

        for channel in range(self.channel_count):
            await self._charger.start_charge(channel)

    async def stop_all(self) -> None:
        if hasattr(self._charger, "stop_charge_multi"):
            await self._charger.stop_charge_multi(ALL_CHANNELS_MASK)
            return

        for channel in range(self.channel_count):
            await self._charger.stop_charge(channel)

    async def write_program(self, channel: int, program: dict) -> None:
        """Write a complete work program to one slot without starting it.

        The MC3000 cannot change a single field (the chemistry in
        particular): the protocol only accepts a whole program, so every
        current/voltage/protection value is written together.
        """
        from skyrc_ble.models import BatteryType, ChannelMode, Mc3000Program

        fields = dict(program)
        fields["battery_type"] = BatteryType[str(fields["battery_type"]).upper()]
        fields["operation"] = ChannelMode[str(fields["operation"]).upper()]

        await self._charger.write_program(channel, Mc3000Program(**fields))

    async def async_get_voltage_curve(self, channel: int) -> VoltageCurve:
        """Read back the voltage curve the charger logged for one slot."""
        charger = self._charger

        if hasattr(charger, "get_voltage_curve_data"):
            curve = await charger.get_voltage_curve_data(channel)
            return VoltageCurve(
                samples_mv=list(curve.samples_mv),
                interval_seconds=curve.interval_seconds,
                unknown_3=curve.unknown_3,
                checksum_ok=curve.checksum_ok,
            )

        if hasattr(charger, "get_voltage_curve"):
            return VoltageCurve(samples_mv=list(await charger.get_voltage_curve(channel)))

        return await self._async_get_voltage_curve_fallback(channel)

    async def _async_get_voltage_curve_fallback(self, channel: int) -> VoltageCurve:
        """Raw 0x56 read, for skyrc-ble versions without a curve helper.

        Kept so the integration still works if someone pins an older
        skyrc-ble than the one in manifest.json.
        """
        from skyrc_ble.mc3000 import CMD_GET_VOLTAGE_CURVE

        charger = self._charger
        chunks: list[bytes] = []
        original_parse_packet = charger._parse_packet

        async def capture_parse_packet(packet):
            packet_bytes = bytes(packet)

            if chunks:
                chunks.append(packet_bytes)
                return

            if (
                len(packet_bytes) >= 2
                and packet_bytes[0] == 0x0F
                and packet_bytes[1] == CMD_GET_VOLTAGE_CURVE
            ):
                chunks.append(packet_bytes)
                return

            await original_parse_packet(packet)

        charger._parse_packet = capture_parse_packet

        try:
            await charger._send_packet(CMD_GET_VOLTAGE_CURVE, [channel])

            loop = asyncio.get_running_loop()
            deadline = loop.time() + VOLTAGE_CURVE_TIMEOUT
            while loop.time() < deadline:
                if len(b"".join(chunks)) >= VOLTAGE_CURVE_FRAME_SIZE:
                    break
                await asyncio.sleep(0.05)

            raw = b"".join(chunks)
        finally:
            charger._parse_packet = original_parse_packet

        if len(raw) < VOLTAGE_CURVE_FRAME_SIZE:
            raise ValueError(
                f"Incomplete voltage curve response: got {len(raw)} bytes, "
                f"expected {VOLTAGE_CURVE_FRAME_SIZE}"
            )

        frame = raw[:VOLTAGE_CURVE_FRAME_SIZE]

        if frame[0] != 0x0F:
            raise ValueError(f"Voltage curve frame does not start with magic byte: 0x{frame[0]:02x}")

        if frame[1] != CMD_GET_VOLTAGE_CURVE:
            raise ValueError(f"Unexpected voltage curve command byte: 0x{frame[1]:02x}")

        if frame[2] != channel:
            raise ValueError(f"Voltage curve channel mismatch: got {frame[2]}, expected {channel}")

        checksum = sum(frame[:-1]) & 0xFF
        checksum_ok = frame[-1] == checksum
        if not checksum_ok:
            raise ValueError(
                f"Voltage curve checksum mismatch: got 0x{frame[-1]:02x}, expected 0x{checksum:02x}"
            )

        sample_bytes = frame[5:-1]
        if len(sample_bytes) != VOLTAGE_CURVE_SAMPLE_BYTES:
            raise ValueError(f"Unexpected voltage curve sample byte length: {len(sample_bytes)}")

        return VoltageCurve(
            samples_mv=[
                int.from_bytes(sample_bytes[i:i + 2], "big")
                for i in range(0, len(sample_bytes), 2)
            ],
            interval_seconds=frame[4],
            unknown_3=frame[3],
            checksum_ok=checksum_ok,
        )
