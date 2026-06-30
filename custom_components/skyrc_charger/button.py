from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = [
        SkyrcRefreshButton(coordinator, entry.entry_id),
        SkyrcStopAllButton(coordinator, entry.entry_id),
    ]
    for slot_index in range(coordinator.channel_count):
        entities.append(SkyrcStartSlotButton(coordinator, entry.entry_id, slot_index))
        entities.append(SkyrcStopSlotButton(coordinator, entry.entry_id, slot_index))

    async_add_entities(entities)


class SkyrcBaseButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = False

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self.entry_id = entry_id

    @property
    def device_info(self):
        device = self.coordinator.data.get("device") if self.coordinator.data else None
        address = getattr(self.coordinator, "address", None) or "skyrc"
        return {
            "identifiers": {(DOMAIN, f"{self.entry_id}-{address}")},
            "name": getattr(device, "name", None) or "SkyRC Charger",
            "manufacturer": getattr(device, "manufacturer", None) or "SkyRC",
            "model": getattr(device, "model", None) or self.coordinator.model.upper(),
        }

    async def _ensure_connected(self):
        if getattr(self.coordinator, "pause_polling", False):
            raise HomeAssistantError(
                "SkyRC companion app mode is active; disable it before sending commands."
            )
        charger = await self.coordinator._ensure_charger()
        if not charger.is_connected:
            await charger.connect()
        return charger


class SkyrcRefreshButton(SkyrcBaseButton):
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_name = "SkyRC Refresh"
        self._attr_unique_id = f"{entry_id}_refresh"
        self._attr_suggested_object_id = f"{entry_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class SkyrcStopAllButton(SkyrcBaseButton):
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_name = "SkyRC Stop All"
        self._attr_unique_id = f"{entry_id}_stop_all"
        self._attr_suggested_object_id = f"{entry_id}_stop_all"

    async def async_press(self) -> None:
        charger = await self._ensure_connected()
        await charger.stop_all()
        await self.coordinator.async_request_refresh()


class SkyrcStartSlotButton(SkyrcBaseButton):
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        super().__init__(coordinator, entry_id)
        self.slot_index = slot_index
        self.slot = slot_index + 1
        self._attr_name = f"SkyRC Slot {self.slot} Start"
        self._attr_unique_id = f"{entry_id}_slot_{self.slot}_start"
        self._attr_suggested_object_id = f"{entry_id}_slot_{self.slot}_start"

    async def async_press(self) -> None:
        charger = await self._ensure_connected()

        kwargs = {}
        # MC5000 needs a chemistry to build a config; reuse the expected
        # chemistry select if it's set to something other than "any".
        entity_id = f"select.{self.entry_id}_slot_{self.slot}_expected_chemistry"
        state = self.hass.states.get(entity_id)
        if state and state.state not in ("any", "unknown", "unavailable", ""):
            kwargs["chemistry"] = state.state

        await charger.start_channel(self.slot_index, **kwargs)
        await self.coordinator.async_request_refresh()


class SkyrcStopSlotButton(SkyrcBaseButton):
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        super().__init__(coordinator, entry_id)
        self.slot_index = slot_index
        self.slot = slot_index + 1
        self._attr_name = f"SkyRC Slot {self.slot} Stop"
        self._attr_unique_id = f"{entry_id}_slot_{self.slot}_stop"
        self._attr_suggested_object_id = f"{entry_id}_slot_{self.slot}_stop"

    async def async_press(self) -> None:
        charger = await self._ensure_connected()
        await charger.stop_channel(self.slot_index)
        await self.coordinator.async_request_refresh()
