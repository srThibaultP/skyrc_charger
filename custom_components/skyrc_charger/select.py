from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CHEMISTRY_OPTIONS, DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = [
        SkyrcChemistrySelect(coordinator, entry.entry_id, slot_index)
        for slot_index in range(coordinator.channel_count)
    ]
    async_add_entities(entities)


class SkyrcChemistrySelect(SelectEntity, RestoreEntity):
    """Expected battery chemistry for a slot.

    Used as a start interlock on the MC3000 (refuses to start if it
    mismatches what the charger reports), and as the chemistry sent in the
    config packet when starting a slot on the MC5000.
    """

    _attr_has_entity_name = False
    _attr_options = CHEMISTRY_OPTIONS
    _attr_icon = "mdi:flask-outline"

    def __init__(self, coordinator, entry_id: str, slot_index: int) -> None:
        self.coordinator = coordinator
        self.entry_id = entry_id
        self.slot_index = slot_index
        self.slot = slot_index + 1

        self._attr_name = f"SkyRC Slot {self.slot} Expected Chemistry"
        self._attr_unique_id = f"{entry_id}_slot_{self.slot}_expected_chemistry"
        self._attr_suggested_object_id = f"{entry_id}_slot_{self.slot}_expected_chemistry"
        self._attr_current_option = "any"

    async def async_added_to_hass(self) -> None:
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in CHEMISTRY_OPTIONS:
            self._attr_current_option = last_state.state

    async def async_select_option(self, option: str) -> None:
        if option not in CHEMISTRY_OPTIONS:
            return
        self._attr_current_option = option
        self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        return self._attr_current_option

    @property
    def device_info(self):
        address = getattr(self.coordinator, "address", None) or "skyrc"
        return {
            "identifiers": {(DOMAIN, f"{self.entry_id}-{address}")},
            "name": "SkyRC Charger",
            "manufacturer": "SkyRC",
            "model": self.coordinator.model.upper(),
        }
