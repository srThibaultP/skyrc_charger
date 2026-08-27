from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import (
    CHEMISTRY_OPTIONS,
    CONF_ADDRESS,
    CONF_MODEL,
    DOMAIN,
    MAX_CHANNELS,
    MC3000_CHEMISTRY_OPTIONS,
    MC3000_MODE_OPTIONS,
    MODE_OPTIONS,
    MODEL_MC3000,
)
from .logging_utils import install_library_log_filter, remove_library_log_filter

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.BUTTON, Platform.SWITCH]

SERVICE_REFRESH = "refresh"
SERVICE_START_SLOT = "start_slot"
SERVICE_STOP_SLOT = "stop_slot"
SERVICE_START_ALL = "start_all"
SERVICE_STOP_ALL = "stop_all"
SERVICE_FETCH_VOLTAGE_CURVE = "fetch_voltage_curve"
SERVICE_WRITE_PROGRAM = "write_program"

ATTR_ENTRY_ID = "entry_id"
ATTR_SLOT = "slot"
ATTR_CHEMISTRY = "chemistry"
ATTR_MODE = "mode"
ATTR_CURRENT_MA = "current_ma"
ATTR_CAPACITY_MAH = "capacity_mah"

# skyrc-ble Mc3000Program fields, as accepted by the write_program service.
PROGRAM_REQUIRED = (
    "battery_type",
    "operation",
    "capacity",
    "charge_current",
    "discharge_current",
    "charge_voltage",
    "discharge_voltage",
    "charge_end_current",
    "discharge_end_current",
)
PROGRAM_OPTIONAL_DEFAULTS = {
    "cycle_time": 0,
    "cycle_count": 1,
    "cycle_type": 0,
    "delta_v": 0,
    "trickle_current": 0,
    "maintenance_voltage": 0,
    "protection_temperature": 0,
    "protection_time": 0,
    "discharge_time": 0,
}

_SERVICES = (
    SERVICE_REFRESH,
    SERVICE_START_SLOT,
    SERVICE_STOP_SLOT,
    SERVICE_START_ALL,
    SERVICE_STOP_ALL,
    SERVICE_FETCH_VOLTAGE_CURVE,
    SERVICE_WRITE_PROGRAM,
)


def _normalize_chemistry(value) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value)).lower()


def _entries(hass: HomeAssistant) -> dict:
    return {
        key: value
        for key, value in hass.data.get(DOMAIN, {}).items()
        if isinstance(value, dict) and "coordinator" in value
    }


def _get_coordinator(hass: HomeAssistant, entry_id: str | None):
    """Resolve the coordinator a service call targets.

    entry_id is optional while a single charger is configured, which keeps
    hand-written scripts and voice assistants simple.
    """
    entries = _entries(hass)

    if not entries:
        raise HomeAssistantError("No SkyRC charger is set up")

    if entry_id is None:
        if len(entries) > 1:
            raise HomeAssistantError(
                "Several SkyRC chargers are set up; pass entry_id to say which one"
            )
        entry_id = next(iter(entries))

    if entry_id not in entries:
        raise HomeAssistantError(f"Unknown SkyRC charger config entry {entry_id!r}")

    return entries[entry_id]["coordinator"]


def _get_expected_chemistry(hass: HomeAssistant, entry_id: str, slot: int) -> str:
    """Read back the 'expected chemistry' select for one slot.

    Resolved through the entity registry by unique_id: guessing the entity_id
    from the entry_id doesn't survive slugification, and the previous
    fallback (scan every select ending in the right suffix) would happily
    pick up another charger's slot.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "select", DOMAIN, f"{entry_id}_slot_{slot}_expected_chemistry"
    )

    if entity_id is None:
        return "any"

    state = hass.states.get(entity_id)
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


def _check_chemistry(hass: HomeAssistant, coordinator, entry_id: str, slot: int) -> str:
    """Raise unless the slot holds the chemistry the user declared.

    Only the MC3000 reports a trustworthy per-slot chemistry, so the
    interlock is enforced there; on the MC5000 the selected value is used as
    the chemistry to write into the program instead.
    """
    expected = _get_expected_chemistry(hass, entry_id, slot)

    if coordinator.model != MODEL_MC3000 or expected == "any":
        return expected

    actual = _get_actual_chemistry(coordinator, slot - 1)
    if actual != expected:
        raise HomeAssistantError(
            f"Refusing to start slot {slot}: expected chemistry {expected!r}, "
            f"but the charger reports {actual!r}"
        )

    return expected


def _start_kwargs(call: ServiceCall, expected_chemistry: str) -> dict:
    kwargs: dict = {}

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

    return kwargs


_ENTRY_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): str})

_SLOT_SCHEMA = _ENTRY_SCHEMA.extend(
    {vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_CHANNELS))}
)

_START_SLOT_SCHEMA = _SLOT_SCHEMA.extend(
    {
        vol.Optional(ATTR_CHEMISTRY): vol.In(CHEMISTRY_OPTIONS),
        vol.Optional(ATTR_MODE): vol.In(MODE_OPTIONS),
        vol.Optional(ATTR_CURRENT_MA): vol.Coerce(int),
        vol.Optional(ATTR_CAPACITY_MAH): vol.Coerce(int),
    }
)

_START_ALL_SCHEMA = _ENTRY_SCHEMA.extend(
    {
        vol.Optional(ATTR_CHEMISTRY): vol.In(CHEMISTRY_OPTIONS),
        vol.Optional(ATTR_MODE): vol.In(MODE_OPTIONS),
        vol.Optional(ATTR_CURRENT_MA): vol.Coerce(int),
        vol.Optional(ATTR_CAPACITY_MAH): vol.Coerce(int),
    }
)


def _write_program_schema() -> vol.Schema:
    def trickle_current(value):
        value = int(value)
        if value % 10:
            raise vol.Invalid("trickle_current must use 10 mA increments")
        return value

    byte = vol.All(vol.Coerce(int), vol.Range(min=0, max=255))
    word = vol.All(vol.Coerce(int), vol.Range(min=0, max=65535))
    current = vol.All(vol.Coerce(float), vol.Range(min=0, max=65.535))

    return _SLOT_SCHEMA.extend(
        {
            vol.Required("battery_type"): vol.In(
                [option for option in MC3000_CHEMISTRY_OPTIONS if option != "any"]
            ),
            vol.Required("operation"): vol.In(MC3000_MODE_OPTIONS),
            vol.Required("capacity"): word,
            vol.Required("charge_current"): current,
            vol.Required("discharge_current"): current,
            vol.Required("charge_voltage"): word,
            vol.Required("discharge_voltage"): word,
            vol.Required("charge_end_current"): word,
            vol.Required("discharge_end_current"): word,
            vol.Optional("cycle_time", default=0): byte,
            vol.Optional("cycle_count", default=1): byte,
            vol.Optional("cycle_type", default=0): byte,
            vol.Optional("delta_v", default=0): byte,
            vol.Optional("trickle_current", default=0): vol.All(byte, trickle_current),
            vol.Optional("maintenance_voltage", default=0): word,
            vol.Optional("protection_temperature", default=0): byte,
            vol.Optional("protection_time", default=0): word,
            vol.Optional("discharge_time", default=0): byte,
        }
    )


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration-wide services, once."""

    def coordinator_for(call: ServiceCall):
        return _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))

    def entry_id_for(call: ServiceCall) -> str:
        entry_id = call.data.get(ATTR_ENTRY_ID)
        if entry_id is not None:
            return entry_id
        return next(iter(_entries(hass)))

    def check_slot(coordinator, slot: int | None) -> None:
        if slot is not None and slot > coordinator.channel_count:
            raise HomeAssistantError(
                f"{coordinator.model.upper()} has {coordinator.channel_count} slots; "
                f"slot {slot} does not exist"
            )

    async def connected(call: ServiceCall):
        """Validate the call, then hand back a connected charger.

        Everything that can be rejected without touching the radio is
        checked first, so a bad call never costs a BLE connection.
        """
        coordinator = coordinator_for(call)
        check_slot(coordinator, call.data.get(ATTR_SLOT))

        if coordinator.pause_polling:
            raise HomeAssistantError(
                "SkyRC companion app mode is active; disable it before sending commands."
            )

        return await coordinator.async_connected_charger(), coordinator

    async def async_handle_refresh(call: ServiceCall) -> None:
        await coordinator_for(call).async_request_refresh()

    async def async_handle_start_slot(call: ServiceCall) -> None:
        slot = call.data[ATTR_SLOT]
        charger, coordinator = await connected(call)

        await coordinator.async_request_refresh()
        expected = _check_chemistry(hass, coordinator, entry_id_for(call), slot)

        kwargs = _start_kwargs(call, expected)
        _LOGGER.info("SkyRC %s: starting slot %s with %s", coordinator.model, slot, kwargs)
        await charger.start_channel(slot - 1, **kwargs)
        await coordinator.async_request_refresh()

    async def async_handle_stop_slot(call: ServiceCall) -> None:
        slot = call.data[ATTR_SLOT]
        charger, coordinator = await connected(call)

        _LOGGER.info("SkyRC %s: stopping slot %s", coordinator.model, slot)
        await charger.stop_channel(slot - 1)
        await coordinator.async_request_refresh()

    async def async_handle_start_all(call: ServiceCall) -> None:
        charger, coordinator = await connected(call)
        entry_id = entry_id_for(call)

        await coordinator.async_request_refresh()

        # Run the same interlock every slot would get individually before
        # arming anything, so a single mismatched cell stops the whole batch.
        # Each slot keeps its own declared chemistry rather than inheriting
        # whatever the last one happened to be.
        per_channel: dict[int, dict] = {}
        for slot in range(1, coordinator.channel_count + 1):
            expected = _check_chemistry(hass, coordinator, entry_id, slot)
            slot_kwargs = _start_kwargs(call, expected)
            if slot_kwargs:
                per_channel[slot - 1] = slot_kwargs

        _LOGGER.info(
            "SkyRC %s: starting all slots with %s", coordinator.model, per_channel or "defaults"
        )
        await charger.start_all(per_channel=per_channel)
        await coordinator.async_request_refresh()

    async def async_handle_stop_all(call: ServiceCall) -> None:
        charger, coordinator = await connected(call)
        _LOGGER.info("SkyRC %s: stopping all slots", coordinator.model)
        await charger.stop_all()
        await coordinator.async_request_refresh()

    async def async_handle_fetch_voltage_curve(call: ServiceCall) -> None:
        coordinator = coordinator_for(call)
        slot = call.data[ATTR_SLOT]
        check_slot(coordinator, slot)

        if not coordinator.supports_voltage_curves:
            raise HomeAssistantError(
                f"{coordinator.model.upper()} does not expose voltage curves"
            )

        await coordinator.async_fetch_voltage_curve(slot - 1)

    async def async_handle_write_program(call: ServiceCall) -> None:
        coordinator = coordinator_for(call)
        slot = call.data[ATTR_SLOT]

        if not coordinator.supports_programs:
            raise HomeAssistantError(
                f"{coordinator.model.upper()} cannot be programmed this way; use start_slot "
                "with chemistry/mode/current instead"
            )

        charger, coordinator = await connected(call)

        program = {key: call.data[key] for key in PROGRAM_REQUIRED}
        for key, default in PROGRAM_OPTIONAL_DEFAULTS.items():
            program[key] = call.data.get(key, default)

        _LOGGER.info(
            "SkyRC %s: writing a complete %s/%s program to slot %s",
            coordinator.model,
            program["battery_type"],
            program["operation"],
            slot,
        )
        await charger.write_program(slot - 1, program)
        await coordinator.async_request_refresh()

    handlers = {
        SERVICE_REFRESH: (async_handle_refresh, _ENTRY_SCHEMA),
        SERVICE_START_SLOT: (async_handle_start_slot, _START_SLOT_SCHEMA),
        SERVICE_STOP_SLOT: (async_handle_stop_slot, _SLOT_SCHEMA),
        SERVICE_START_ALL: (async_handle_start_all, _START_ALL_SCHEMA),
        SERVICE_STOP_ALL: (async_handle_stop_all, _ENTRY_SCHEMA),
        SERVICE_FETCH_VOLTAGE_CURVE: (async_handle_fetch_voltage_curve, _SLOT_SCHEMA),
        SERVICE_WRITE_PROGRAM: (async_handle_write_program, _write_program_schema()),
    }

    for service, (handler, schema) in handlers.items():
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a SkyRC charger (MC3000 or MC5000) from a config entry."""
    from .coordinator import SkyrcChargerCoordinator

    domain_data = hass.data.setdefault(DOMAIN, {})

    if "library_log_filter" not in domain_data:
        domain_data["library_log_filter"] = install_library_log_filter()

    address = entry.data[CONF_ADDRESS]
    model = entry.data.get(CONF_MODEL, MODEL_MC3000)

    coordinator = SkyrcChargerCoordinator(hass, address, model, device_name=entry.title)
    domain_data[entry.entry_id] = {"coordinator": coordinator, "model": model}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_register_services(hass)

    hass.async_create_task(coordinator.async_refresh())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if not unload_ok:
        return False

    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.pop(entry.entry_id, None)

    if entry_data is not None:
        coordinator = entry_data["coordinator"]
        charger = coordinator.charger
        if charger is not None:
            try:
                await charger.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("SkyRC %s: disconnect on unload failed: %r", coordinator.model, err)

    # Services and the log filter are integration-wide; drop them only once
    # the last charger is gone.
    if not _entries(hass):
        for service in _SERVICES:
            hass.services.async_remove(DOMAIN, service)

        log_filter = domain_data.pop("library_log_filter", None)
        if log_filter is not None:
            remove_library_log_filter(log_filter)

    return unload_ok
