from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN
from .entity import SkyrcEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[ButtonEntity] = [
        SkyrcRefreshButton(coordinator, entry.entry_id),
        SkyrcStartAllButton(coordinator, entry.entry_id),
        SkyrcStopAllButton(coordinator, entry.entry_id),
    ]

    for slot_index in range(coordinator.channel_count):
        entities.append(SkyrcStartSlotButton(coordinator, entry.entry_id, slot_index))
        entities.append(SkyrcStopSlotButton(coordinator, entry.entry_id, slot_index))
        if coordinator.supports_voltage_curves:
            entities.append(SkyrcFetchVoltageCurveButton(coordinator, entry.entry_id, slot_index))

    async_add_entities(entities)


class SkyrcBaseButton(SkyrcEntity, ButtonEntity):
    """A button always stays pressable, even while the charger is offline."""

    @property
    def available(self) -> bool:
        return True


class SkyrcRefreshButton(SkyrcBaseButton):
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_name = "Refresh"
        self._attr_unique_id = f"{entry_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class SkyrcStopAllButton(SkyrcBaseButton):
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_name = "Stop All"
        self._attr_unique_id = f"{entry_id}_stop_all"

    async def async_press(self) -> None:
        charger = await self.async_connected_charger()
        await charger.stop_all()
        await self.coordinator.async_request_refresh()


class SkyrcStartAllButton(SkyrcBaseButton):
    """Start every slot, through the chemistry-checked start service."""

    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_name = "Start All"
        self._attr_unique_id = f"{entry_id}_start_all"

    async def async_press(self) -> None:
        self.raise_if_companion_mode()
        await self.hass.services.async_call(
            DOMAIN,
            "start_all",
            {"entry_id": self.entry_id},
            blocking=True,
        )


class SkyrcStartSlotButton(SkyrcBaseButton):
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        super().__init__(coordinator, entry_id)
        self.slot_index = slot_index
        self.slot = slot_index + 1
        self._attr_name = f"Slot {self.slot} Start"
        self._attr_unique_id = f"{entry_id}_slot_{self.slot}_start"

    async def async_press(self) -> None:
        # Go through the service so the button gets the same chemistry
        # interlock (and, on the MC5000, the same program defaults) as a
        # scripted start.
        self.raise_if_companion_mode()
        await self.hass.services.async_call(
            DOMAIN,
            "start_slot",
            {"entry_id": self.entry_id, "slot": self.slot},
            blocking=True,
        )


class SkyrcStopSlotButton(SkyrcBaseButton):
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        super().__init__(coordinator, entry_id)
        self.slot_index = slot_index
        self.slot = slot_index + 1
        self._attr_name = f"Slot {self.slot} Stop"
        self._attr_unique_id = f"{entry_id}_slot_{self.slot}_stop"

    async def async_press(self) -> None:
        charger = await self.async_connected_charger()
        await charger.stop_channel(self.slot_index)
        await self.coordinator.async_request_refresh()


class SkyrcFetchVoltageCurveButton(SkyrcBaseButton):
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        super().__init__(coordinator, entry_id)
        self.slot_index = slot_index
        self.slot = slot_index + 1
        self._attr_name = f"Slot {self.slot} Fetch Voltage Curve"
        self._attr_unique_id = f"{entry_id}_slot_{self.slot}_fetch_voltage_curve"

    async def async_press(self) -> None:
        self.raise_if_companion_mode()
        await self.coordinator.async_fetch_voltage_curve(self.slot_index)
