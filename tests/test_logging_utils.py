"""The BLE stack's routine noise must not reach the Home Assistant log."""

from __future__ import annotations

import logging
import unittest

from _harness import load

logging_utils = load("logging_utils")
RecoverableSkyrcBleLogFilter = logging_utils.RecoverableSkyrcBleLogFilter


def _record(name: str, message: str, level: int = logging.WARNING) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, message, (), None)


class RecoverableSkyrcBleLogFilterTest(unittest.TestCase):
    def test_disconnect_is_demoted_to_debug(self) -> None:
        record = _record("skyrc_ble.device", "%s: Disconnected from address %s")

        self.assertTrue(RecoverableSkyrcBleLogFilter().filter(record))
        self.assertEqual(record.levelno, logging.DEBUG)
        self.assertEqual(record.levelname, "DEBUG")

    def test_response_timeout_is_demoted_to_debug(self) -> None:
        record = _record("skyrc_ble.mc3000", "%s: Timeout waiting for response notification")

        self.assertTrue(RecoverableSkyrcBleLogFilter().filter(record))
        self.assertEqual(record.levelno, logging.DEBUG)

    def test_failed_connect_is_demoted_to_debug(self) -> None:
        # Emitted once per poll for as long as the charger is switched off,
        # and recovered by itself the moment it comes back. The coordinator
        # reports the outage once, so the library's copy is noise.
        record = _record(
            "skyrc_ble.device", "%s: Failed to connect to address %s", logging.ERROR
        )

        self.assertTrue(RecoverableSkyrcBleLogFilter().filter(record))
        self.assertEqual(record.levelno, logging.DEBUG)

    def test_unreviewed_library_message_keeps_its_level(self) -> None:
        record = _record("skyrc_ble.device", "%s: Something nobody has looked at yet")

        self.assertTrue(RecoverableSkyrcBleLogFilter().filter(record))
        self.assertEqual(record.levelno, logging.WARNING)

    def test_install_and_remove_are_symmetric(self) -> None:
        logger = logging.getLogger("skyrc_ble.device")
        before = len(logger.filters)

        log_filter = logging_utils.install_library_log_filter()
        self.assertIn(log_filter, logger.filters)

        logging_utils.remove_library_log_filter(log_filter)
        self.assertEqual(len(logger.filters), before)


if __name__ == "__main__":
    unittest.main()
