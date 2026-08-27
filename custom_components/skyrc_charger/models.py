"""Common data model shared by the MC3000 and MC5000 drivers.

Both drivers expose the same shape of data to the coordinator/entities so
that sensors, buttons, etc. don't need to know which charger they're
talking to.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChannelData:
    """Normalized state of a single charging slot."""

    index: int  # 0-based
    status: str | None = None  # idle / charging / discharging / done / paused / ...
    chemistry: str | None = None  # liion, nimh, ... or None if unknown/empty
    mode: str | None = None  # charge / storage / discharge / cycle / refresh / break_in
    voltage: float | None = None  # volts
    current: float | None = None  # amps
    capacity: int | None = None  # mAh
    temperature: float | None = None  # celsius
    resistance: float | None = None  # milliohms
    time: int | None = None  # seconds elapsed
    cycle_count: int | None = None
    led: str | None = None


@dataclass
class DeviceData:
    """Normalized device-level info."""

    name: str = "SkyRC Charger"
    address: str = ""
    manufacturer: str = "SkyRC"
    model: str = ""
    hw_version: str | None = None
    sw_version: str | None = None
    input_voltage: float | None = None
    # MC3000-only settings, exposed as diagnostic sensors when present.
    temperature_unit: str | None = None
    display_mode: str | None = None
    cooling_fan_mode: str | None = None
    system_beep: bool | None = None
    screensaver: bool | None = None


@dataclass
class ChargerState:
    device: DeviceData = field(default_factory=DeviceData)
    channels: list[ChannelData] = field(default_factory=list)


@dataclass
class VoltageCurve:
    """One slot's logged voltage curve, as read back from the charger."""

    samples_mv: list[int] = field(default_factory=list)
    interval_seconds: int | None = None
    unknown_3: int | None = None
    checksum_ok: bool | None = None


class ChargerClient:
    """Common interface both drivers must implement.

    The coordinator and entity platforms only talk to this interface, so
    swapping MC3000 <-> MC5000 is just a matter of instantiating a
    different class in coordinator.py.
    """

    channel_count: int = 0

    #: Whether the charger can replay a logged voltage curve for a slot.
    supports_voltage_curves: bool = False
    #: Whether a complete charging program can be written to a slot.
    supports_programs: bool = False

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError

    def set_ble_device(self, ble_device) -> None:
        """Point the client at a freshly discovered BLEDevice.

        Home Assistant hands out a new BLEDevice whenever the charger is
        re-advertised (and, with Bluetooth proxies, the connection path can
        change between adverts). Reusing a stale one is the usual cause of
        reconnect loops, so the coordinator refreshes it before every
        reconnect.
        """
        raise NotImplementedError

    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def async_update(self) -> ChargerState:
        """Poll the charger and return a fresh ChargerState."""
        raise NotImplementedError

    async def start_channel(self, channel: int, **kwargs) -> None:
        """Start charging/discharging on a channel.

        For the MC3000 this starts the program already configured on the
        device. For the MC5000 the protocol requires a full configuration
        to be sent first (there is no "use last config" command), so
        kwargs may carry chemistry/mode/current/capacity overrides; sane
        defaults are used otherwise.
        """
        raise NotImplementedError

    async def stop_channel(self, channel: int) -> None:
        raise NotImplementedError

    async def start_all(self, per_channel: dict[int, dict] | None = None, **kwargs) -> None:
        """Start every channel.

        `per_channel` carries overrides that differ from slot to slot (each
        slot's declared chemistry, typically); `kwargs` applies to all of
        them. Defaults to starting the channels one by one.
        """
        per_channel = per_channel or {}
        for channel in range(self.channel_count):
            await self.start_channel(channel, **{**kwargs, **per_channel.get(channel, {})})

    async def stop_all(self) -> None:
        raise NotImplementedError

    async def async_get_voltage_curve(self, channel: int) -> VoltageCurve:
        """Read back the logged voltage curve for one slot."""
        raise NotImplementedError

    async def write_program(self, channel: int, program: dict) -> None:
        """Write a complete charging program to one slot, without starting it."""
        raise NotImplementedError
