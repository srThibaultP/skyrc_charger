from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_ADDRESS, CONF_MODEL, DOMAIN, MODEL_MC3000

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.BUTTON, Platform.SWITCH]

SERVICE_REFRESH = "refresh"
SERVICE_START_SLOT = "start_slot"
SERVICE_STOP_SLOT = "stop_slot"
SERVICE_STOP_ALL = "stop_all"

ATTR_SLOT = "slot"
ATTR_CHEMISTRY = "chemistry"
ATTR_MODE = "mode"
ATTR_CURRENT_MA = "current_ma"
ATTR_CAPACITY_MAH = "capacity_mah"


def _normalize_chemistry(value) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value)).lower()


def _get_expected_chemistry(hass: HomeAssistant, entry_id: str, slot: int) -> str:
    entity_id = f"select.{entry_id}_slot_{slot}_expected_chemistry"
    # Entity ids are generated with suggested_object_id below; fall back to
    # scanning states if the exact id can't be guessed.
    state = hass.states.get(entity_id)
    if state is None:
        for st in hass.states.async_all("select"):
            if st.entity_id.endswith(f"slot_{slot}_expected_chemistry"):
                state = st
                break

    if state is None:
        return "any"

    value = str(state.state).lower()
    return "any" if value in ("unknown", "unavailable", "") else value


def _get_actual_chemistry(coordinator, channel: int) -> str | None:
    if not coordinator.data:
        return None
    channels = coordinator.data.get("channels") or []
    if channel >= len(channels):
        return None
    return _normalize_chemistry(getattr(channels[channel], "chemistry", None))


async def _get_connected_charger(hass: HomeAssistant, entry_id: str):
    coordinator = hass.data[DOMAIN][entry_id]["coordinator"]

    if getattr(coordinator, "pause_polling", False):
        raise HomeAssistantError(
            "SkyRC companion app mode is active; disable it before sending commands."
        )

    charger = await coordinator._ensure_charger()
    if not charger.is_connected:
        await charger.connect()

    return charger, coordinator


def _slot_schema(max_slot: int):
    import voluptuous as vol

    return vol.Schema(
        {
            vol.Required("entry_id"): str,
            vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=max_slot)),
            vol.Optional(ATTR_CHEMISTRY): str,
            vol.Optional(ATTR_MODE): str,
            vol.Optional(ATTR_CURRENT_MA): vol.Coerce(int),
            vol.Optional(ATTR_CAPACITY_MAH): vol.Coerce(int),
        }
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a SkyRC charger (MC3000 or MC5000) from a config entry."""
    from .coordinator import SkyrcChargerCoordinator

    hass.data.setdefault(DOMAIN, {})

    address = entry.data[CONF_ADDRESS]
    model = entry.data.get(CONF_MODEL, MODEL_MC3000)

    coordinator = SkyrcChargerCoordinator(hass, address, model)
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator, "model": model}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def async_handle_refresh(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        await hass.data[DOMAIN][entry_id]["coordinator"].async_request_refresh()

    async def async_handle_start_slot(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        slot = call.data[ATTR_SLOT]
        channel = slot - 1

        charger, coordinator = await _get_connected_charger(hass, entry_id)
        await coordinator.async_request_refresh()

        expected_chemistry = _get_expected_chemistry(hass, entry_id, slot)
        actual_chemistry = _get_actual_chemistry(coordinator, channel)

        if (
            coordinator.model == MODEL_MC3000
            and expected_chemistry != "any"
            and actual_chemistry != expected_chemistry
        ):
            raise HomeAssistantError(
                f"Refusing to start slot {slot}: expected chemistry "
                f"{expected_chemistry!r}, but charger reports {actual_chemistry!r}"
            )

        kwargs = {}
        if ATTR_CHEMISTRY in call.data:
            kwargs["chemistry"] = call.data[ATTR_CHEMISTRY]
        elif expected_chemistry != "any":
            kwargs["chemistry"] = expected_chemistry
        if ATTR_MODE in call.data:
            kwargs["mode"] = call.data[ATTR_MODE]
        if ATTR_CURRENT_MA in call.data:
            kwargs["current"] = call.data[ATTR_CURRENT_MA]
        if ATTR_CAPACITY_MAH in call.data:
            kwargs["capacity"] = call.data[ATTR_CAPACITY_MAH]

        _LOGGER.info("SkyRC %s: starting slot %s with %s", coordinator.model, slot, kwargs)
        await charger.start_channel(channel, **kwargs)
        await coordinator.async_request_refresh()

    async def async_handle_stop_slot(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        slot = call.data[ATTR_SLOT]
        channel = slot - 1

        charger, coordinator = await _get_connected_charger(hass, entry_id)
        _LOGGER.info("SkyRC %s: stopping slot %s", coordinator.model, slot)
        await charger.stop_channel(channel)
        await coordinator.async_request_refresh()

    async def async_handle_stop_all(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        charger, coordinator = await _get_connected_charger(hass, entry_id)
        _LOGGER.info("SkyRC %s: stopping all slots", coordinator.model)
        await charger.stop_all()
        await coordinator.async_request_refresh()

    max_slot = coordinator.channel_count

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        import voluptuous as vol

        hass.services.async_register(
            DOMAIN, SERVICE_REFRESH, async_handle_refresh,
            schema=vol.Schema({vol.Required("entry_id"): str}),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_START_SLOT):
        hass.services.async_register(
            DOMAIN, SERVICE_START_SLOT, async_handle_start_slot, schema=_slot_schema(max_slot)
        )

    if not hass.services.has_service(DOMAIN, SERVICE_STOP_SLOT):
        import voluptuous as vol

        hass.services.async_register(
            DOMAIN, SERVICE_STOP_SLOT, async_handle_stop_slot,
            schema=vol.Schema(
                {
                    vol.Required("entry_id"): str,
                    vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=max_slot)),
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_STOP_ALL):
        import voluptuous as vol

        hass.services.async_register(
            DOMAIN, SERVICE_STOP_ALL, async_handle_stop_all,
            schema=vol.Schema({vol.Required("entry_id"): str}),
        )

    hass.async_create_task(coordinator.async_refresh())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
