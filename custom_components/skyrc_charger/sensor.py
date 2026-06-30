from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


@dataclass(frozen=True)
class SkyrcSensorDescription:
    key: str
    name: str
    native_unit_of_measurement: str | None
    device_class: SensorDeviceClass | None
    state_class: SensorStateClass | None
    icon: str | None
    value_fn: Callable[[Any], Any]


def _format_seconds(value: Any) -> str | None:
    if value is None:
        return None
    try:
        total_seconds = int(value)
    except (TypeError, ValueError):
        return None
    if total_seconds < 0:
        return None
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


CHANNEL_SENSORS: tuple[SkyrcSensorDescription, ...] = (
    SkyrcSensorDescription("status", "Status", None, None, None, "mdi:state-machine", lambda ch: ch.status),
    SkyrcSensorDescription("battery_type", "Battery Type", None, None, None, "mdi:battery", lambda ch: ch.chemistry),
    SkyrcSensorDescription("mode", "Mode", None, None, None, "mdi:battery-sync", lambda ch: ch.mode),
    SkyrcSensorDescription(
        "voltage", "Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,
        SensorStateClass.MEASUREMENT, None, lambda ch: ch.voltage,
    ),
    SkyrcSensorDescription(
        "current", "Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT,
        SensorStateClass.MEASUREMENT, None, lambda ch: ch.current,
    ),
    SkyrcSensorDescription(
        "capacity", "Capacity", "mAh", None, SensorStateClass.TOTAL_INCREASING,
        "mdi:battery-plus", lambda ch: ch.capacity,
    ),
    SkyrcSensorDescription(
        "temperature", "Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT, None, lambda ch: ch.temperature,
    ),
    SkyrcSensorDescription(
        "resistance", "Internal Resistance", "mΩ", None, SensorStateClass.MEASUREMENT,
        "mdi:omega", lambda ch: ch.resistance,
    ),
    SkyrcSensorDescription("time", "Elapsed Time", None, None, None, "mdi:timer-outline", lambda ch: _format_seconds(ch.time)),
)

DEVICE_SENSORS: tuple[SkyrcSensorDescription, ...] = (
    SkyrcSensorDescription(
        "input_voltage", "Input Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE,
        SensorStateClass.MEASUREMENT, None, lambda dev: dev.input_voltage,
    ),
)


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[SensorEntity] = []
    for description in DEVICE_SENSORS:
        entities.append(SkyrcDeviceSensor(coordinator, entry.entry_id, description))

    for slot_index in range(coordinator.channel_count):
        for description in CHANNEL_SENSORS:
            entities.append(SkyrcChannelSensor(coordinator, entry.entry_id, slot_index, description))

    async_add_entities(entities, update_before_add=False)


class SkyrcBaseSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id: str, description: SkyrcSensorDescription) -> None:
        super().__init__(coordinator)
        self.entry_id = entry_id
        self.entity_description_custom = description
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_icon = description.icon
        if description.key == "voltage":
            self._attr_suggested_display_precision = 3

    @property
    def device_info(self):
        device = self.coordinator.data.get("device") if self.coordinator.data else None
        address = getattr(self.coordinator, "address", None) or "skyrc"
        return {
            "identifiers": {(DOMAIN, f"{self.entry_id}-{address}")},
            "name": getattr(device, "name", None) or "SkyRC Charger",
            "manufacturer": getattr(device, "manufacturer", None) or "SkyRC",
            "model": getattr(device, "model", None) or self.coordinator.model.upper(),
            "sw_version": getattr(device, "sw_version", None),
            "hw_version": getattr(device, "hw_version", None),
        }


class SkyrcDeviceSensor(SkyrcBaseSensor):
    def __init__(self, coordinator, entry_id, description) -> None:
        super().__init__(coordinator, entry_id, description)
        self._attr_name = description.name
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self.entity_description_custom.value_fn(self.coordinator.data["device"])


class SkyrcChannelSensor(SkyrcBaseSensor):
    def __init__(self, coordinator, entry_id, slot_index, description) -> None:
        super().__init__(coordinator, entry_id, description)
        self.slot_index = slot_index
        self._attr_name = f"Slot {slot_index + 1} {description.name}"
        self._attr_unique_id = f"{entry_id}_slot_{slot_index + 1}_{description.key}"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        channels = self.coordinator.data["channels"]
        if self.slot_index >= len(channels):
            return None
        return self.entity_description_custom.value_fn(channels[self.slot_index])
