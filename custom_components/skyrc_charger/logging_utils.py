"""Logging helpers for the BLE stack underneath this integration.

The chargers drop their BLE link constantly: the MC3000 firmware closes the
connection after a few seconds of idling, and both models disappear from the
air entirely while the mains side is switched off. Every one of those is
recovered on the next poll, but the libraries below us log each one at
WARNING/ERROR. At a ten second poll interval that is ~360 lines an hour, i.e.
the several thousand lines of noise a day this used to produce.

The records are kept (so `logger: skyrc_ble: debug` still shows everything);
they are just demoted to DEBUG.
"""

from __future__ import annotations

import logging

# (logger name, exact format string) pairs. Matching on the format string
# rather than the formatted message means this keeps working regardless of
# the device name/address interpolated into it, and it can never accidentally
# demote a message we haven't reviewed.
_RECOVERABLE_LIBRARY_MESSAGES = {
    ("skyrc_ble.device", "%s: Disconnected from address %s"),
    ("skyrc_ble.device", "%s: Failed to connect to address %s"),
    ("skyrc_ble.mc3000", "%s: Timeout waiting for response notification"),
}

_LOGGER_NAMES = {name for name, _message in _RECOVERABLE_LIBRARY_MESSAGES}


class RecoverableSkyrcBleLogFilter(logging.Filter):
    """Demote expected, automatically recovered BLE messages to debug."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Adjust the level while preserving the record for debug logging."""
        if (record.name, record.msg) in _RECOVERABLE_LIBRARY_MESSAGES:
            record.levelno = logging.DEBUG
            record.levelname = logging.getLevelName(logging.DEBUG)

        return True


def install_library_log_filter() -> RecoverableSkyrcBleLogFilter:
    """Install and return a filter for noisy recoverable library messages."""
    log_filter = RecoverableSkyrcBleLogFilter()

    for logger_name in _LOGGER_NAMES:
        logging.getLogger(logger_name).addFilter(log_filter)

    return log_filter


def remove_library_log_filter(log_filter: RecoverableSkyrcBleLogFilter) -> None:
    """Remove a previously installed library log filter."""
    for logger_name in _LOGGER_NAMES:
        logging.getLogger(logger_name).removeFilter(log_filter)
