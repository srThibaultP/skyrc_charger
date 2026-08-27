from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import SkyrcEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [SkyrcCompanionAppModeSwitch(coordinator, entry.entry_id)]
    if coordinator.supports_voltage_curves:
        entities.append(SkyrcAutoFetchVoltageCurvesSwitch(coordinator, entry.entry_id))

    async_add_entities(entities)


class SkyrcBaseSwitch(SkyrcEntity, SwitchEntity):
    """These switches control the integration, so they stay usable offline."""

    @property
    def available(self) -> bool:
        return True


class SkyrcCompanionAppModeSwitch(SkyrcBaseSwitch):
    """Releases the BLE connection so the official SkyRC app can connect."""

    _attr_name = "Companion App Mode"
    _attr_icon = "mdi:bluetooth-off"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_companion_app_mode"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.pause_polling)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_enable_companion_app_mode()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_disable_companion_app_mode()
        self.async_write_ha_state()


class SkyrcAutoFetchVoltageCurvesSwitch(SkyrcBaseSwitch):
    """Keep the voltage curves of the working slots refreshed automatically."""

    _attr_name = "Auto Fetch Voltage Curves"
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_auto_fetch_voltage_curves"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.auto_fetch_voltage_curves)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_auto_fetch_voltage_curves(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_auto_fetch_voltage_curves(False)
        self.async_write_ha_state()
