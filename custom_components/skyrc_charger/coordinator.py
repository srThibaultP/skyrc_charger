from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CHANNELS_BY_MODEL, DOMAIN, MODEL_NAMES, MODEL_MC3000, MODEL_MC5000
from .mc3000_client import BLE_NAME_PATTERNS as MC3000_BLE_NAME_PATTERNS, Mc3000Client
from .mc5000_client import BLE_NAME_PATTERNS as MC5000_BLE_NAME_PATTERNS, Mc5000Client
from .models import ChargerClient

_LOGGER = logging.getLogger(__name__)

SCAN_TIMEOUT = 30.0
UPDATE_INTERVAL = timedelta(seconds=10)

# A direct BleakScanner sweep blocks for SCAN_TIMEOUT and fights Home
# Assistant's own bluetooth manager for the adapter. It is only a safety net
# for setups where HA doesn't know the charger yet, so run it sparingly
# instead of on every failed poll.
DIRECT_SCAN_INTERVAL = 300.0

# Stale data is served while the charger is unreachable so the dashboard
# doesn't flicker on the routine one-poll dropouts. After this many failures
# in a row (~5 minutes) the entities go unavailable instead, rather than
# presenting hours-old readings as current.
STALE_AFTER_FAILURES = 30

# While an outage lasts, repeat the warning about it at most once an hour.
FAILURE_REMINDER_EVERY = 360

CLIENT_BY_MODEL = {
    MODEL_MC3000: Mc3000Client,
    MODEL_MC5000: Mc5000Client,
}

BLE_NAME_PATTERNS_BY_MODEL = {
    MODEL_MC3000: MC3000_BLE_NAME_PATTERNS,
    MODEL_MC5000: MC5000_BLE_NAME_PATTERNS,
}


def _name_matches(name: str, patterns: tuple[str, ...]) -> bool:
    name_lower = (name or "").lower()
    return any(pattern in name_lower for pattern in patterns)


class SkyrcChargerCoordinator(DataUpdateCoordinator):
    """Coordinator that polls either an MC3000 or MC5000 over BLE."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        model: str,
        device_name: str | None = None,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.address = address
        self.model = model
        # Taken from the config entry title so it is stable from the very
        # first setup: entity ids are derived from the device name, and one
        # that only settles after the first successful poll would produce
        # entity ids named after a charger we hadn't reached yet.
        self.device_name = device_name or MODEL_NAMES.get(model, "SkyRC Charger")
        self.channel_count = CHANNELS_BY_MODEL[model]

        self.charger: ChargerClient | None = None
        self._last_device = None
        self._last_good_data = None
        self.pause_polling = False

        # Noise control: these chargers drop their link constantly and every
        # drop is recovered on the next poll. Log the transition into and out
        # of a degraded state, not each of the ~360 failures an hour.
        self._consecutive_failures = 0
        self._last_failure_repr: str | None = None
        self._last_fallback_address: str | None = None
        self._last_direct_scan_monotonic = 0.0

        # Voltage curves (MC3000 only).
        self.voltage_curves: dict[int, dict] = {}
        self._last_slot_currents: dict[int, float | None] = {}
        self._slot_current_zero_elapsed: dict[int, int | None] = {}

        self.auto_fetch_voltage_curves = False
        self.auto_fetch_voltage_curve_interval_seconds = 30
        self._last_auto_fetch_voltage_curves_monotonic = 0.0

    @property
    def supports_voltage_curves(self) -> bool:
        return CLIENT_BY_MODEL[self.model].supports_voltage_curves

    @property
    def supports_programs(self) -> bool:
        return CLIENT_BY_MODEL[self.model].supports_programs

    @property
    def available(self) -> bool:
        """Whether the data being served is still worth showing."""
        return self.last_update_success and self._consecutive_failures < STALE_AFTER_FAILURES

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------

    def _find_device_via_ha_bluetooth(self):
        """Ask Home Assistant's Bluetooth manager for a connectable device.

        This is the path that works with ESPHome/Shelly Bluetooth proxies and
        that keeps the BLEDevice (and its connection route) fresh; the direct
        Bleak scan below only sees locally attached adapters.
        """
        device = bluetooth.async_ble_device_from_address(
            self.hass,
            self.address.upper(),
            connectable=True,
        )

        if device is None:
            _LOGGER.debug(
                "SkyRC %s: HA Bluetooth has no connectable device for %s",
                self.model,
                self.address,
            )

        return device

    async def _find_device_via_bleak_fallback(self):
        """Direct Bleak sweep, for setups HA's manager doesn't cover."""
        from bleak import BleakScanner

        now = time.monotonic()
        if now - self._last_direct_scan_monotonic < DIRECT_SCAN_INTERVAL:
            return None

        self._last_direct_scan_monotonic = now
        patterns = BLE_NAME_PATTERNS_BY_MODEL[self.model]

        _LOGGER.debug("SkyRC %s: direct Bleak scan for %s", self.model, self.address)
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)

        fallback = None
        for device in devices:
            name = device.name or ""
            address = device.address or ""

            if address.upper() == self.address.upper():
                return device

            if _name_matches(name, patterns):
                fallback = device

        if fallback is None:
            return None

        # Only shout about a substitution the first time we settle on it;
        # repeating it every poll is exactly the noise we're removing.
        if self._last_fallback_address != fallback.address:
            self._last_fallback_address = fallback.address
            _LOGGER.warning(
                "SkyRC %s: %s not found, falling back to look-alike device %s (%s)",
                self.model,
                self.address,
                fallback.address,
                fallback.name,
            )

        return fallback

    async def _find_device(self):
        device = self._find_device_via_ha_bluetooth()
        if device is not None:
            return device

        try:
            return await self._find_device_via_bleak_fallback()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("SkyRC %s: direct Bleak fallback failed: %r", self.model, err)
            return None

    async def _ensure_charger(self) -> ChargerClient:
        if self.pause_polling:
            raise UpdateFailed(f"SkyRC {self.model} polling paused for companion app mode")

        if self.charger is not None and self.charger.is_connected:
            return self.charger

        device = await self._find_device() or self._last_device
        if device is None:
            raise UpdateFailed(f"SkyRC {self.model} not found at {self.address}")

        self._last_device = device

        if self.charger is None:
            self.charger = CLIENT_BY_MODEL[self.model](device)
        else:
            # Reconnecting with a stale BLEDevice is the classic cause of
            # endless reconnect failures, so always hand over the newest one.
            self.charger.set_ble_device(device)

        return self.charger

    async def async_connected_charger(self) -> ChargerClient:
        """Return a connected client, for service calls and button presses."""
        charger = await self._ensure_charger()

        if not charger.is_connected:
            await charger.connect()

        return charger

    # ------------------------------------------------------------------
    # Companion app mode
    # ------------------------------------------------------------------

    async def async_enable_companion_app_mode(self) -> None:
        _LOGGER.info("SkyRC %s: enabling companion app mode", self.model)
        self.pause_polling = True

        if self.charger is not None:
            try:
                if self.charger.is_connected:
                    await self.charger.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("SkyRC %s: disconnect failed: %r", self.model, err)

        self.charger = None
        self._last_device = None
        self.async_update_listeners()

    async def async_disable_companion_app_mode(self) -> None:
        _LOGGER.info("SkyRC %s: disabling companion app mode", self.model)
        self.pause_polling = False
        self.charger = None
        self._last_device = None
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Voltage curves (MC3000)
    # ------------------------------------------------------------------

    def _track_current_transitions(self, data: dict) -> None:
        """Remember when each slot's current last fell to zero.

        The charger keeps logging curve samples after it has finished, so
        knowing when the current stopped lets the dashboard trim the flat
        tail off the plot.
        """
        for slot_index, channel in enumerate(data.get("channels") or []):
            if channel is None:
                continue

            current = getattr(channel, "current", None)
            if current is None:
                continue

            try:
                current_value = float(current)
            except (TypeError, ValueError):
                continue

            elapsed = getattr(channel, "time", None)
            previous_current = self._last_slot_currents.get(slot_index)

            if current_value > 0.001:
                self._slot_current_zero_elapsed[slot_index] = None
            elif previous_current is not None and previous_current > 0.001:
                self._slot_current_zero_elapsed[slot_index] = (
                    int(elapsed) if elapsed is not None else None
                )
                _LOGGER.debug(
                    "SkyRC %s: slot %s current reached zero at elapsed=%s",
                    self.model,
                    slot_index + 1,
                    self._slot_current_zero_elapsed[slot_index],
                )

            self._last_slot_currents[slot_index] = current_value

    async def async_fetch_voltage_curve(self, slot_index: int) -> dict:
        """Fetch the voltage curve for one slot on demand."""
        if slot_index not in range(self.channel_count):
            raise ValueError(f"Invalid slot index {slot_index}")

        if not self.supports_voltage_curves:
            raise UpdateFailed(f"SkyRC {self.model} does not expose voltage curves")

        if self.pause_polling:
            raise UpdateFailed(f"SkyRC {self.model} polling paused for companion app mode")

        charger = await self.async_connected_charger()

        try:
            curve = await charger.async_get_voltage_curve(slot_index)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(
                f"SkyRC {self.model}: voltage curve fetch failed for slot {slot_index + 1}: {err!r}"
            ) from err

        samples_mv = list(curve.samples_mv)
        nonzero = [value for value in samples_mv if value > 0]
        nonzero_indices = [idx for idx, value in enumerate(samples_mv) if value > 0]
        last_nonzero_index = nonzero_indices[-1] if nonzero_indices else None

        stop_elapsed = self._slot_current_zero_elapsed.get(slot_index)
        plot_until_index = last_nonzero_index
        plot_reason = "last_nonzero_sample"

        if stop_elapsed is not None and curve.interval_seconds and last_nonzero_index is not None:
            estimated_stop_index = int(stop_elapsed / curve.interval_seconds)
            if 0 < estimated_stop_index < last_nonzero_index:
                plot_until_index = estimated_stop_index
                plot_reason = "current_zero_elapsed"

        result = {
            "slot": slot_index + 1,
            "channel": slot_index,
            "sample_count": len(samples_mv),
            "nonzero_sample_count": len(nonzero),
            "min_nonzero_mv": min(nonzero) if nonzero else None,
            "max_nonzero_mv": max(nonzero) if nonzero else None,
            "interval_seconds": curve.interval_seconds,
            "unknown_3": curve.unknown_3,
            "checksum_ok": curve.checksum_ok,
            "current_zero_elapsed": stop_elapsed,
            "plot_until_index": plot_until_index,
            "plot_reason": plot_reason,
            "samples_mv": samples_mv,
            "samples_v": [round(value / 1000, 3) for value in samples_mv],
            "last_fetched": datetime.now().isoformat(timespec="seconds"),
        }

        self.voltage_curves[slot_index] = result
        self.async_update_listeners()

        return result

    async def async_set_auto_fetch_voltage_curves(self, enabled: bool) -> None:
        self.auto_fetch_voltage_curves = bool(enabled)
        self.async_update_listeners()

        if enabled:
            await self.async_auto_fetch_voltage_curves(force=True)

    async def async_auto_fetch_voltage_curves(self, force: bool = False) -> None:
        """Fetch curves for the slots that are currently working, throttled."""
        if not self.auto_fetch_voltage_curves or self.pause_polling:
            return

        now = time.monotonic()
        if (
            not force
            and now - self._last_auto_fetch_voltage_curves_monotonic
            < self.auto_fetch_voltage_curve_interval_seconds
        ):
            return

        if not self.data:
            return

        active_slots = []
        for slot_index, channel in enumerate(self.data.get("channels") or []):
            if channel is None:
                continue
            try:
                current = float(getattr(channel, "current", 0) or 0)
            except (TypeError, ValueError):
                continue
            if current > 0.001:
                active_slots.append(slot_index)

        if not active_slots:
            return

        self._last_auto_fetch_voltage_curves_monotonic = now

        for slot_index in active_slots:
            try:
                await self.async_fetch_voltage_curve(slot_index)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "SkyRC %s: auto voltage curve fetch failed for slot %s: %r",
                    self.model,
                    slot_index + 1,
                    err,
                )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _build_data(self, state):
        return {"state": state, "device": state.device, "channels": state.channels}

    async def _async_drop_charger(self) -> None:
        """Close the link before forgetting the client.

        The chargers accept a single BLE client at a time, so abandoning a
        half-open connection makes the next poll fail too.
        """
        charger, self.charger = self.charger, None
        if charger is None:
            return

        try:
            await charger.disconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("SkyRC %s: disconnect after failure failed: %r", self.model, err)

    def _note_success(self) -> None:
        if self._consecutive_failures:
            _LOGGER.info(
                "SkyRC %s: back online after %s failed poll(s)",
                self.model,
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._last_failure_repr = None

    def _note_failure(self, err: Exception) -> None:
        """Log a poll failure once per outage rather than once per poll."""
        self._consecutive_failures += 1
        failure_repr = repr(err)

        first = self._consecutive_failures == 1
        reminder = self._consecutive_failures % FAILURE_REMINDER_EVERY == 0

        if first or reminder:
            _LOGGER.warning(
                "SkyRC %s: poll failed %s time(s) in a row; retrying every %ss and serving "
                "the last known values meanwhile: %s",
                self.model,
                self._consecutive_failures,
                int(UPDATE_INTERVAL.total_seconds()),
                failure_repr,
            )
        else:
            _LOGGER.debug(
                "SkyRC %s: still unreachable after %s polls: %s",
                self.model,
                self._consecutive_failures,
                failure_repr,
            )

        self._last_failure_repr = failure_repr

    async def _async_update_data(self):
        if self.pause_polling:
            if self._last_good_data is not None:
                return self._last_good_data
            raise UpdateFailed(f"SkyRC {self.model} polling paused for companion app mode")

        try:
            charger = await self._ensure_charger()

            if not charger.is_connected:
                await charger.connect()
                if not charger.is_connected:
                    raise ConnectionError(f"SkyRC {self.model} BLE connection was not established")

            state = await charger.async_update()
            data = self._build_data(state)

            self._track_current_transitions(data)
            self._last_good_data = data
            self._note_success()

            await self.async_auto_fetch_voltage_curves()

            return data

        except Exception as err:  # noqa: BLE001
            await self._async_drop_charger()
            self._note_failure(err)

            if self._last_good_data is not None:
                return self._last_good_data

            raise UpdateFailed(f"Error communicating with SkyRC {self.model}: {err!r}") from err
