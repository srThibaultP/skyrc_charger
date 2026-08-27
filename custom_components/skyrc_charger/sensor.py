from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfTemperature
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import SkyrcEntity


@dataclass(frozen=True)
class SkyrcSensorDescription:
    key: str
    name: str
    native_unit_of_measurement: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    icon: str | None = None
    entity_category: EntityCategory | None = None
    value_fn: Callable[[Any], Any] = lambda _: None


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
    SkyrcSensorDescription("status", "Status", icon="mdi:state-machine", value_fn=lambda ch: ch.status),
    SkyrcSensorDescription("battery_type", "Battery Type", icon="mdi:battery", value_fn=lambda ch: ch.chemistry),
    SkyrcSensorDescription("mode", "Mode", icon="mdi:battery-sync", value_fn=lambda ch: ch.mode),
    SkyrcSensorDescription(
        "voltage", "Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda ch: ch.voltage,
    ),
    SkyrcSensorDescription(
        "current", "Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda ch: ch.current,
    ),
    SkyrcSensorDescription(
        # MEASUREMENT, not TOTAL_INCREASING: the charger resets this counter
        # to zero at the start of every run, which a total-increasing sensor
        # would read as a meter rollover and add to the long-term statistic.
        "capacity", "Capacity",
        native_unit_of_measurement="mAh",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-plus",
        value_fn=lambda ch: ch.capacity,
    ),
    SkyrcSensorDescription(
        "temperature", "Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda ch: ch.temperature,
    ),
    SkyrcSensorDescription(
        "resistance", "Internal Resistance",
        native_unit_of_measurement="mΩ",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:omega",
        value_fn=lambda ch: ch.resistance,
    ),
    SkyrcSensorDescription(
        "time", "Elapsed Time", icon="mdi:timer-outline",
        value_fn=lambda ch: _format_seconds(ch.time),
    ),
    SkyrcSensorDescription(
        "cycle_count", "Cycle Count", icon="mdi:autorenew",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ch: ch.cycle_count,
    ),
)

DEVICE_SENSORS: tuple[SkyrcSensorDescription, ...] = (
    SkyrcSensorDescription(
        "input_voltage", "Input Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda dev: dev.input_voltage,
    ),
    SkyrcSensorDescription(
        "temperature_unit", "Temperature Unit", icon="mdi:temperature-celsius",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda dev: dev.temperature_unit,
    ),
    SkyrcSensorDescription(
        "display_mode", "Display Mode", icon="mdi:monitor",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda dev: dev.display_mode,
    ),
    SkyrcSensorDescription(
        "cooling_fan_mode", "Cooling Fan Mode", icon="mdi:fan",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda dev: dev.cooling_fan_mode,
    ),
    SkyrcSensorDescription(
        "system_beep", "System Beep", icon="mdi:volume-high",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda dev: dev.system_beep,
    ),
    SkyrcSensorDescription(
        "screensaver", "Screensaver", icon="mdi:monitor-screenshot",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda dev: dev.screensaver,
    ),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = [
        SkyrcDeviceSensor(coordinator, entry.entry_id, description)
        for description in DEVICE_SENSORS
    ]

    for slot_index in range(coordinator.channel_count):
        entities.extend(
            SkyrcChannelSensor(coordinator, entry.entry_id, slot_index, description)
            for description in CHANNEL_SENSORS
        )
        if coordinator.supports_voltage_curves:
            entities.append(SkyrcVoltageCurveSensor(coordinator, entry.entry_id, slot_index))

    async_add_entities(entities)


class SkyrcBaseSensor(SkyrcEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id: str, description: SkyrcSensorDescription) -> None:
        super().__init__(coordinator, entry_id)
        self.entity_description_custom = description
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category
        if description.key == "voltage":
            self._attr_suggested_display_precision = 3


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
        channel = channels[self.slot_index]
        if channel is None:
            return None
        return self.entity_description_custom.value_fn(channel)


class SkyrcVoltageCurveSensor(SkyrcEntity, SensorEntity):
    """The voltage curve the charger logged for one slot.

    The state is the number of usable samples; the curve itself rides along
    as attributes so a dashboard chart can plot it.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:chart-line"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        super().__init__(coordinator, entry_id)
        self.slot_index = slot_index
        self._attr_name = f"Slot {slot_index + 1} Voltage Curve Points"
        self._attr_unique_id = f"{entry_id}_slot_{slot_index + 1}_voltage_curve_points"

    @property
    def native_value(self):
        data = self.coordinator.voltage_curves.get(self.slot_index)
        return data["nonzero_sample_count"] if data else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.voltage_curves.get(self.slot_index)
        if not data:
            return {}
        return dict(data)
