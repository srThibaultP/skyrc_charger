"""Shared entity plumbing for the SkyRC charger platforms."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def build_device_info(coordinator, entry_id: str) -> dict:
    """Describe the charger, filling in what the last poll told us."""
    device = coordinator.data.get("device") if coordinator.data else None
    address = getattr(coordinator, "address", None) or "skyrc"

    info = {
        "identifiers": {(DOMAIN, f"{entry_id}-{address}")},
        "connections": {(CONNECTION_BLUETOOTH, address)} if address != "skyrc" else set(),
        "name": getattr(coordinator, "device_name", None) or "SkyRC Charger",
        "manufacturer": getattr(device, "manufacturer", None) or "SkyRC",
        "model": getattr(device, "model", None) or coordinator.model.upper(),
    }

    for key in ("sw_version", "hw_version"):
        value = getattr(device, key, None)
        if value:
            info[key] = str(value)

    return info


class SkyrcEntity(CoordinatorEntity):
    """Base for every entity bound to a charger's coordinator.

    has_entity_name is on throughout, so Home Assistant prefixes both the
    friendly name and the generated entity id with the charger's name. That
    is what makes ids read `button.skyrc_mc5000_slot_1_start` instead of
    being built from the config entry's ULID.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self.entry_id = entry_id

    @property
    def device_info(self) -> dict:
        return build_device_info(self.coordinator, self.entry_id)

    @property
    def available(self) -> bool:
        return self.coordinator.available

    def raise_if_companion_mode(self) -> None:
        """Block commands while the BLE link is handed over to the app."""
        if getattr(self.coordinator, "pause_polling", False):
            raise HomeAssistantError(
                "SkyRC companion app mode is active; disable it before sending commands."
            )

    async def async_connected_charger(self):
        self.raise_if_companion_mode()
        return await self.coordinator.async_connected_charger()
