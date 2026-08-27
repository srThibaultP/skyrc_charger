"""The coordinator must survive BLE dropouts without flooding the log.

Regression cover for the behaviour that produced thousands of warning lines
a day: one warning per failed poll, at a ten second interval.
"""

from __future__ import annotations

import logging
import unittest

from _harness import load

coordinator_module = load("coordinator")
models = load("models")
SkyrcChargerCoordinator = coordinator_module.SkyrcChargerCoordinator
UpdateFailed = coordinator_module.UpdateFailed

LOGGER_NAME = coordinator_module.__name__


class FakeCharger:
    """A charger that can be told to fail."""

    supports_voltage_curves = False
    supports_programs = False

    def __init__(self) -> None:
        self.is_connected = True
        self.fail = False
        self.devices: list[object] = []

    def set_ble_device(self, device) -> None:
        self.devices.append(device)

    async def connect(self) -> None:
        self.is_connected = True

    async def async_update(self):
        if self.fail:
            self.is_connected = False
            raise OSError("le charger a disparu")
        return models.ChargerState(
            device=models.DeviceData(model="MC5000"),
            channels=[models.ChannelData(index=i, current=0.0) for i in range(4)],
        )


def _coordinator(charger: FakeCharger) -> SkyrcChargerCoordinator:
    coordinator = SkyrcChargerCoordinator.__new__(SkyrcChargerCoordinator)
    coordinator.model = "mc5000"
    coordinator.address = "AA:BB:CC:DD:EE:FF"
    coordinator.channel_count = 4
    coordinator.pause_polling = False
    coordinator.charger = charger
    coordinator.data = None
    coordinator.last_update_success = True
    coordinator._last_device = object()
    coordinator._last_good_data = {"state": "last-known-good"}
    coordinator._consecutive_failures = 0
    coordinator._last_failure_repr = None
    coordinator._last_fallback_address = None
    coordinator._last_direct_scan_monotonic = 0.0
    coordinator.voltage_curves = {}
    coordinator._last_slot_currents = {}
    coordinator._slot_current_zero_elapsed = {}
    coordinator.auto_fetch_voltage_curves = False
    coordinator.auto_fetch_voltage_curve_interval_seconds = 30
    coordinator._last_auto_fetch_voltage_curves_monotonic = 0.0

    async def ensure_charger():
        return charger

    coordinator._ensure_charger = ensure_charger
    return coordinator


class QuietRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_poll_keeps_cached_data(self) -> None:
        charger = FakeCharger()
        charger.fail = True
        coordinator = _coordinator(charger)
        cached = coordinator._last_good_data

        with self.assertLogs(LOGGER_NAME, level=logging.DEBUG):
            result = await coordinator._async_update_data()

        self.assertIs(result, cached)
        self.assertIsNone(coordinator.charger)

    async def test_a_long_outage_warns_once_not_once_per_poll(self) -> None:
        charger = FakeCharger()
        charger.fail = True
        coordinator = _coordinator(charger)

        with self.assertLogs(LOGGER_NAME, level=logging.DEBUG) as captured:
            for _ in range(60):
                await coordinator._async_update_data()

        warnings = [line for line in captured.output if line.startswith("WARNING")]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertEqual(coordinator._consecutive_failures, 60)

    async def test_recovery_is_reported(self) -> None:
        charger = FakeCharger()
        charger.fail = True
        coordinator = _coordinator(charger)

        with self.assertLogs(LOGGER_NAME, level=logging.DEBUG):
            await coordinator._async_update_data()

        charger.fail = False
        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            await coordinator._async_update_data()

        self.assertTrue(any("back online" in line for line in captured.output))
        self.assertEqual(coordinator._consecutive_failures, 0)

    async def test_failure_without_cached_data_still_raises(self) -> None:
        charger = FakeCharger()
        charger.fail = True
        coordinator = _coordinator(charger)
        coordinator._last_good_data = None

        with self.assertLogs(LOGGER_NAME, level=logging.DEBUG):
            with self.assertRaises(UpdateFailed):
                await coordinator._async_update_data()

    async def test_entities_go_unavailable_once_the_data_is_stale(self) -> None:
        charger = FakeCharger()
        charger.fail = True
        coordinator = _coordinator(charger)

        with self.assertLogs(LOGGER_NAME, level=logging.DEBUG):
            for _ in range(coordinator_module.STALE_AFTER_FAILURES - 1):
                await coordinator._async_update_data()
            self.assertTrue(coordinator.available)

            await coordinator._async_update_data()
            self.assertFalse(coordinator.available)


class DeviceRefreshTest(unittest.IsolatedAsyncioTestCase):
    """A reconnect must use the BLEDevice Home Assistant knows about now."""

    async def test_reconnect_adopts_the_freshly_discovered_device(self) -> None:
        charger = FakeCharger()
        charger.is_connected = False
        coordinator = _coordinator(charger)

        fresh_device = object()

        async def find_device():
            return fresh_device

        coordinator._find_device = find_device
        del coordinator._ensure_charger

        returned = await SkyrcChargerCoordinator._ensure_charger(coordinator)

        self.assertIs(returned, charger)
        self.assertEqual(charger.devices, [fresh_device])
        self.assertIs(coordinator._last_device, fresh_device)

    async def test_no_discovery_while_the_link_is_up(self) -> None:
        charger = FakeCharger()
        coordinator = _coordinator(charger)
        del coordinator._ensure_charger

        async def find_device():
            raise AssertionError("should not scan while connected")

        coordinator._find_device = find_device

        self.assertIs(await SkyrcChargerCoordinator._ensure_charger(coordinator), charger)
        self.assertEqual(charger.devices, [])


if __name__ == "__main__":
    unittest.main()
