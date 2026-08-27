"""Constants for the SkyRC Charger (MC3000 / MC5000) integration."""

DOMAIN = "skyrc_charger"

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_MODEL = "model"

DEFAULT_NAME = "SkyRC Charger"

MODEL_MC3000 = "mc3000"
MODEL_MC5000 = "mc5000"

# Labels for the model picker in the config flow.
MODELS = {
    MODEL_MC3000: "SkyRC MC3000 (4 slots)",
    MODEL_MC5000: "SkyRC MC5000 (4 slots)",
}

# Default title of a config entry, which is also the device name entity ids
# are built from - so keep it short and free of parentheses.
MODEL_NAMES = {
    MODEL_MC3000: "SkyRC MC3000",
    MODEL_MC5000: "SkyRC MC5000",
}

# Both chargers expose four slots. This used to say 8 for the MC3000, which
# made the integration create four phantom slots and made stop_all raise
# ValueError inside skyrc-ble for channels 4-7.
CHANNELS_BY_MODEL = {
    MODEL_MC3000: 4,
    MODEL_MC5000: 4,
}

MAX_CHANNELS = max(CHANNELS_BY_MODEL.values())

# The MC3000 advertises one of these exact local names (same list as
# skyrc-ble's MC3000_BLUETOOTH_NAMES). The MC5000 uses a generic BLE module
# so its name varies; those patterns are matched case-insensitively as a
# substring. Keep the MC5000 patterns reasonably specific: they are only
# used to *suggest* devices in the config flow, never to silently connect to
# whatever happens to be nearby.
MC3000_BLE_NAMES = ("SimpleBLEPeripheral", "Charger", "HitecCharger")
MC3000_BLE_NAME_PATTERNS = tuple(name.lower() for name in MC3000_BLE_NAMES) + ("mc3000", "skyrc")
MC5000_BLE_NAME_PATTERNS = ("mc5000", "skyrc", "#charger", "telinkse", "charger")

# Both chargers speak over the same TelinkSE-style serial service.
BLE_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
BLE_CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Chemistry names are model specific: they mirror the enum/byte values each
# charger actually reports, so the "expected chemistry" interlock can compare
# them to what the device sends back. Mixing the two lists (as a single
# shared list did) meant the MC3000 interlock could never match.
MC3000_CHEMISTRY_OPTIONS = [
    "any",
    "liion",
    "life",
    "liion_4_35",
    "nimh",
    "nicd",
    "nizn",
    "eneloop",
    "ram",
    "batlto",
]

MC5000_CHEMISTRY_OPTIONS = [
    "any",
    "liion",
    "liion_hv",
    "lifepo4",
    "nimh",
    "nicd",
    "eneloop",
    "nizn",
    "ram",
    "lto",
    "naion",
]

CHEMISTRY_OPTIONS_BY_MODEL = {
    MODEL_MC3000: MC3000_CHEMISTRY_OPTIONS,
    MODEL_MC5000: MC5000_CHEMISTRY_OPTIONS,
}

# Union of both lists, for service schemas that are registered once for the
# whole integration.
CHEMISTRY_OPTIONS = sorted(set(MC3000_CHEMISTRY_OPTIONS) | set(MC5000_CHEMISTRY_OPTIONS))

MC3000_MODE_OPTIONS = ["charge", "refresh", "storage", "breakin", "discharge", "cycle"]
MC5000_MODE_OPTIONS = ["charge", "storage", "discharge", "cycle", "refresh", "break_in"]

MODE_OPTIONS = sorted(set(MC3000_MODE_OPTIONS) | set(MC5000_MODE_OPTIONS))
