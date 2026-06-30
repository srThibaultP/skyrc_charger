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
    resistance: int | None = None  # milliohms
    time: int | None = None  # seconds elapsed


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


@dataclass
class ChargerState:
    device: DeviceData = field(default_factory=DeviceData)
    channels: list[ChannelData] = field(default_factory=list)


class ChargerClient:
    """Common interface both drivers must implement.

    The coordinator and entity platforms only talk to this interface, so
    swapping MC3000 <-> MC5000 is just a matter of instantiating a
    different class in coordinator.py.
    """

    channel_count: int = 0

    @property
    def is_connected(self) -> bool:
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

    async def stop_all(self) -> None:
        raise NotImplementedError
