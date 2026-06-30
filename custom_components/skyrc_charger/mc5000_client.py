"""MC5000 driver - native Python re-implementation of the BLE protocol.

Reverse-engineered protocol reference:
https://github.com/rssdev10/skyrc-mc-rs/blob/main/docs/PROTOCOL.md

Packet format: 0x0F | length | command | data... | checksum
checksum = sum(command..data) mod 256
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from .const import MC5000_BLE_NAME_PATTERNS
from .models import ChannelData, ChargerClient, ChargerState, DeviceData

_LOGGER = logging.getLogger(__name__)

BLE_NAME_PATTERNS = MC5000_BLE_NAME_PATTERNS

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

START_BYTE = 0x0F

CMD_GREETING = 0x06
CMD_HANDSHAKE = 0x57
CMD_VERSION = 0x74
CMD_SETTINGS = 0x65
CMD_SLOT_QUERY = 0xFE
CMD_STATUS = 0x91
CMD_START_STOP = 0x93
CMD_CONFIG = 0x94

NOTIFY_TIMEOUT = 5.0

CHEMISTRY_TO_BYTE = {
    "liion": 0x00,
    "liion_hv": 0x01,
    "lifepo4": 0x02,
    "nimh": 0x03,
    "nicd": 0x04,
    "eneloop": 0x05,
    "nizn": 0x06,
    "ram": 0x07,
    "lto": 0x08,
    "naion": 0x09,
}
BYTE_TO_CHEMISTRY = {v: k for k, v in CHEMISTRY_TO_BYTE.items()}

# The byte at offset 19 of the 0x91 status response encodes chemistry
# using a DIFFERENT, much less certain scheme than the 0x94 config command
# above (which is fully confirmed). The protocol doc flags this field as
# "observed, unconfirmed" with only two known values. We deliberately keep
# this separate so a status-response chemistry byte never gets silently
# reinterpreted using the (different) config byte values.
STATUS_CHEM_BYTE_TO_NAME = {
    0x00: "liion",
    0x02: "nimh",
}

# target / cutoff voltage (mV) per chemistry, used to build a sane default
# 0x94 config when the caller doesn't override them.
DEFAULT_TARGET_MV = {
    "liion": 4200, "liion_hv": 4350, "lifepo4": 3650, "nimh": 1650,
    "nicd": 1650, "eneloop": 1650, "nizn": 1900, "ram": 1650,
    "lto": 2850, "naion": 4000,
}
DEFAULT_CUTOFF_MV = {
    "liion": 3200, "liion_hv": 3400, "lifepo4": 2900, "nimh": 900,
    "nicd": 900, "eneloop": 900, "nizn": 1100, "ram": 900,
    "lto": 1800, "naion": 2000,
}

# default "secondary" field (data[33-34]): storage voltage (mV) for
# lithium-class chemistries, else 110% of capacity (mAh) for nickel/alkaline
DEFAULT_SECONDARY_MV = {
    "liion": 3800, "liion_hv": 3900, "lifepo4": 3300, "lto": 2400, "naion": 3500,
}

MODE_TO_BYTE = {
    "charge": 0x00,
    "storage": 0x01,
    "discharge": 0x02,
    "cycle": 0x03,
    "refresh": 0x04,
    "break_in": 0x05,
}

STATUS_BYTE_NAME = {
    0x00: "idle",
    0x01: "charging_cc",
    0x02: "charging_cv",
    0x03: "charging",
    0x04: "done",
    0x05: "charging_trickle",
    0x06: "charging",
    0x07: "discharging",
    0x09: "paused",
}

CHANNEL_BITMASK = [0x01, 0x02, 0x04, 0x08]  # index -> bitmask, 4 slots


def _checksum(payload: bytes) -> int:
    """payload = command byte + data bytes (no start/length/checksum)."""
    return sum(payload) & 0xFF


def _build_packet(command: int, data: bytes = b"") -> bytes:
    body = bytes([command]) + data
    length = len(body) + 1  # +1 for checksum byte itself
    checksum = _checksum(body)
    return bytes([START_BYTE, length]) + body + bytes([checksum])


def _u16(value: int) -> bytes:
    return int(value).to_bytes(2, "big")


class Mc5000Client(ChargerClient):
    channel_count = 4

    def __init__(self, ble_device) -> None:
        self._device = ble_device
        self._client: BleakClient | None = None
        self._notify_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._device_info = DeviceData(name="SkyRC MC5000", model="MC5000")
        self._connected = False
        # last known config per channel, used so "start" can be called
        # without forcing the user to re-specify chemistry every time
        self._last_config: dict[int, dict] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    def _notify_handler(self, _characteristic, data: bytearray) -> None:
        self._notify_queue.put_nowait(bytes(data))

    async def connect(self) -> None:
        self._client = BleakClient(self._device)
        await self._client.connect()
        await self._client.start_notify(CHAR_UUID, self._notify_handler)
        self._connected = True

        # Drain the unsolicited greeting (0x06) if/when it arrives.
        try:
            await asyncio.wait_for(self._notify_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        # Required init sequence; without it the device ACKs config (0x94)
        # but silently ignores start/stop (0x93).
        await self._send(CMD_VERSION, b"\x00\x00\x00\x00\x00")
        await self._send(CMD_SETTINGS, b"\x00\x00")
        await self._send(CMD_SLOT_QUERY, b"\x00")

        device_info = await self._query_device_info()
        if device_info:
            self._device_info = device_info

    async def disconnect(self) -> None:
        self._connected = False
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("MC5000 disconnect error: %r", err)
            self._client = None

    async def _send(self, command: int, data: bytes = b"", expect_response: bool = True) -> bytes | None:
        if self._client is None:
            raise RuntimeError("MC5000 not connected")

        packet = _build_packet(command, data)
        # drain stale notifications
        while not self._notify_queue.empty():
            self._notify_queue.get_nowait()

        await self._client.write_gatt_char(CHAR_UUID, packet, response=False)

        if not expect_response:
            return None

        try:
            return await asyncio.wait_for(self._notify_queue.get(), timeout=NOTIFY_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning("MC5000: no response to command 0x%02x", command)
            return None

    async def _query_device_info(self) -> DeviceData | None:
        addr = getattr(self._device, "address", "") or ""
        suffix = bytes.fromhex(addr.replace(":", "")[-8:].rjust(8, "0")) if addr else b"\x00\x00\x00\x00"
        request = bytes([0x00]) + suffix + b"\xc1\xa4" + b"\x00" * 10
        resp = await self._send(CMD_HANDSHAKE, request)
        if not resp or len(resp) < 18:
            return None

        try:
            serial = resp[4:11].decode("ascii", errors="ignore")
            fw_major, fw_minor = resp[12], resp[13]
            hw_major, hw_minor = resp[14], resp[15]
            return DeviceData(
                name="SkyRC MC5000",
                address=addr,
                manufacturer="SkyRC",
                model="MC5000",
                sw_version=f"{fw_major}.{fw_minor:02d}",
                hw_version=f"{hw_major}.{hw_minor:02d}",
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("MC5000: could not parse handshake response: %r", err)
            return None

    @staticmethod
    def _parse_channel(index: int, payload: bytes) -> ChannelData:
        # payload = full notification with start+length stripped, i.e.
        # payload[0]=command(0x91) payload[1]=channel ... payload[-1]=checksum
        #
        # Full-packet offsets per docs/PROTOCOL.md (offset 0 = start byte):
        #   3=channel 4=status 5=current_raw 6-7=voltage 8-9=unused
        #   10-11=capacity 12-13=unused 14-15=elapsed 16-17=resistance
        #   18=delta-V 19=chemistry 20=unused 21=slot_index 22=checksum
        # Since payload already strips the 2 leading bytes (start+length),
        # subtract 2 from every offset above to index into `payload`.
        # Full responses are 21 bytes (this stripped payload); BLE MTU can
        # truncate notifications to ~18 bytes, dropping chemistry/slot
        # index/checksum. Don't throw the whole channel away for that.
        if len(payload) < 16:
            return ChannelData(index=index, status="unavailable")

        status_byte = payload[2]
        current_raw = payload[3]
        voltage_mv = int.from_bytes(payload[4:6], "big")
        capacity = int.from_bytes(payload[8:10], "big")
        elapsed = int.from_bytes(payload[12:14], "big")
        resistance = int.from_bytes(payload[14:16], "big")
        chem_byte = payload[17] if len(payload) > 17 else None

        if status_byte == 0x00:
            # Status 0x00 is overloaded; disambiguate per protocol doc.
            if voltage_mv == 0:
                status = "empty"
            elif current_raw > 0:
                status = "charging"
            elif elapsed > 0 and capacity > 0:
                status = "done"
            else:
                status = "idle"
            current_ma = current_raw * 4
        else:
            status = STATUS_BYTE_NAME.get(status_byte, f"unknown_{status_byte:02x}")
            multiplier = 10 if status_byte == 0x07 else 4
            current_ma = current_raw * multiplier

        return ChannelData(
            index=index,
            status=status,
            chemistry=(
                STATUS_CHEM_BYTE_TO_NAME.get(chem_byte, f"unknown_0x{chem_byte:02x}")
                if chem_byte is not None
                else None
            ),
            mode=None,
            voltage=round(voltage_mv / 1000, 3) if voltage_mv else 0.0,
            current=round(current_ma / 1000, 3),
            capacity=capacity,
            temperature=None,
            resistance=resistance,
            time=elapsed,
        )

    async def async_update(self) -> ChargerState:
        if not self.is_connected:
            raise RuntimeError("MC5000 not connected")

        channels: list[ChannelData] = []
        for index, bitmask in enumerate(CHANNEL_BITMASK):
            resp = await self._send(CMD_STATUS, bytes([bitmask]))
            if resp is None or len(resp) < 4:
                channels.append(ChannelData(index=index, status="unavailable"))
                continue
            # resp = full packet [0f, len, 91, channel, status...]; strip
            # start+length, keep cmd..checksum for parsing convenience.
            body = resp[2:]
            channels.append(self._parse_channel(index, body))

        return ChargerState(device=self._device_info, channels=channels)

    def _build_config_payload(self, channel: int, **kwargs) -> bytes:
        chemistry = kwargs.get("chemistry") or "liion"
        mode = kwargs.get("mode") or "charge"
        charge_current = int(kwargs.get("current", 1000))
        discharge_current = int(kwargs.get("discharge_current", charge_current))
        capacity = int(kwargs.get("capacity", 3000))
        target_mv = int(kwargs.get("target_mv", DEFAULT_TARGET_MV.get(chemistry, 4200)))
        cutoff_mv = int(kwargs.get("cutoff_mv", DEFAULT_CUTOFF_MV.get(chemistry, 3200)))
        charge_cutoff_ma = int(kwargs.get("charge_cutoff_ma", 100))
        discharge_cutoff_ma = int(kwargs.get("discharge_cutoff_ma", 100))
        delta_peak_mv = int(kwargs.get("delta_peak_mv", 6))
        trickle_x10 = int(kwargs.get("trickle_ma", 0)) // 10
        keep_mv = int(kwargs.get("keep_mv", 0))
        cutoff_timer_min = int(kwargs.get("cutoff_timer_min", 0))
        max_time_min = int(kwargs.get("max_time_min", 300))
        chem_byte = CHEMISTRY_TO_BYTE.get(chemistry, 0x00)
        if "secondary" in kwargs:
            secondary = int(kwargs["secondary"])
        elif chemistry in DEFAULT_SECONDARY_MV:
            secondary = DEFAULT_SECONDARY_MV[chemistry]
        else:
            secondary = int(capacity * 1.1)
        charge_rest_min = int(kwargs.get("charge_rest_min", 10))
        discharge_rest_min = int(kwargs.get("discharge_rest_min", 10))

        data = bytearray(40)
        data[0] = CHANNEL_BITMASK[channel] if channel is not None else 0x00
        data[1] = MODE_TO_BYTE.get(mode, 0x00)
        data[2:4] = _u16(charge_current)
        data[4:6] = _u16(discharge_current)
        data[6:8] = _u16(capacity)
        data[8:10] = _u16(target_mv)
        data[10:12] = _u16(cutoff_mv)
        data[12:14] = _u16(charge_cutoff_ma)
        data[14:16] = _u16(discharge_cutoff_ma)
        data[16:18] = _u16(charge_rest_min)
        data[18:20] = _u16(discharge_rest_min)
        data[20] = int(kwargs.get("cycle_count", 1))
        data[21] = int(kwargs.get("cycle_direction", 0))
        data[22] = delta_peak_mv
        data[23] = trickle_x10
        data[24:26] = _u16(keep_mv)
        data[26] = 0x3C
        data[27:29] = _u16(cutoff_timer_min)
        data[29:31] = _u16(max_time_min)
        data[31] = 0x00
        data[32] = chem_byte
        data[33:35] = _u16(secondary)
        # data[35:40] padding stays zero
        return bytes(data)

    async def start_channel(self, channel: int, **kwargs) -> None:
        """Send a fresh 0x94 config for this channel then start it.

        The MC5000 protocol has no "use last configured program" command
        (unlike the MC3000); the app always pushes a complete config
        before starting. We do the same here, defaulting any field the
        caller doesn't supply.
        """
        config = self._last_config.get(channel, {})
        config.update(kwargs)
        self._last_config[channel] = config

        payload = self._build_config_payload(channel, **config)
        await self._send(CMD_CONFIG, payload)
        await self._send(CMD_START_STOP, bytes([CHANNEL_BITMASK[channel]]))

    async def stop_channel(self, channel: int) -> None:
        # IMPORTANT CAVEAT: the MC5000 0x93 command appears to treat its
        # channel-bitmask argument as a "set the complete active slot set
        # to exactly this" instruction, not a per-slot stop. The reference
        # protocol doc is ambiguous/contradictory on this point (one frame
        # is annotated as being captured "while stopping a single slot",
        # but the same byte pattern is documented elsewhere as "start
        # slot"). To avoid accidentally starting the wrong slot, we do not
        # rely on that behaviour: stopping a single channel falls back to
        # stopping everything, which is the one action that is solidly
        # validated in the protocol doc.
        _LOGGER.info(
            "MC5000: per-slot stop is not reliably documented; stopping all "
            "slots instead of channel %s",
            channel,
        )
        await self.stop_all()

    async def stop_all(self) -> None:
        await self._send(CMD_START_STOP, b"\x00")
