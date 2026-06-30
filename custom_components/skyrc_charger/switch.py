from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    async_add_entities([SkyrcCompanionAppModeSwitch(coordinator, entry.entry_id)])


class SkyrcCompanionAppModeSwitch(CoordinatorEntity, SwitchEntity):
    """Releases the BLE connection so the official SkyRC app can connect."""

    _attr_has_entity_name = False
    _attr_name = "SkyRC Companion App Mode"
    _attr_icon = "mdi:cellphone-link"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self.entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_companion_app_mode"
        self._attr_suggested_object_id = f"{entry_id}_companion_app_mode"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.pause_polling)

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

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_enable_companion_app_mode()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_disable_companion_app_mode()
        self.async_write_ha_state()
