"""Constants for the SkyRC Charger (MC3000 / MC5000) integration."""

DOMAIN = "skyrc_charger"

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_MODEL = "model"

DEFAULT_NAME = "SkyRC Charger"

MODEL_MC3000 = "mc3000"
MODEL_MC5000 = "mc5000"

MODELS = {
    MODEL_MC3000: "SkyRC MC3000 (8 slots)",
    MODEL_MC5000: "SkyRC MC5000 (4 slots)",
}

CHANNELS_BY_MODEL = {
    MODEL_MC3000: 8,
    MODEL_MC5000: 4,
}

# BLE advertised names used for auto-discovery.
# BLE advertised names used for auto-discovery. These chargers use a
# generic BLE module (TelinkSE) so the advertised name is often NOT the
# product name — match by substring, not exact name. Patterns are matched
# case-insensitively against a substring of the advertised local name.
MC3000_BLE_NAME_PATTERNS = ("charger", "skyrc", "mc3000", "telinkse")
MC5000_BLE_NAME_PATTERNS = ("mc5000", "skyrc", "#charger", "telinkse", "charger")

CHEMISTRY_OPTIONS = [
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

MODE_OPTIONS = [
    "charge",
    "storage",
    "discharge",
    "cycle",
    "refresh",
    "break_in",
]
