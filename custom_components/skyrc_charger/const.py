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
MC3000_BLE_NAMES = ("Charger", "SimpleBLEPeripheral", "HitecCharger")
MC5000_BLE_NAMES = ("MC5000", "SkyRC MC5000", "BT_MC5000")

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
