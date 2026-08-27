"""Import the integration without Home Assistant installed.

The modules under test are pure Python, but they import `homeassistant`,
`bleak`, `bleak_retry_connector` and `skyrc_ble` at module level. Rather
than pull the whole of Home Assistant into CI, stub those imports with the
handful of names the integration actually touches, then load the package
modules straight from disk.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).parents[1]
PACKAGE = "custom_components.skyrc_charger"
PACKAGE_PATH = ROOT / "custom_components" / "skyrc_charger"


class UpdateFailed(Exception):
    """Stand-in for homeassistant.helpers.update_coordinator.UpdateFailed."""


class DataUpdateCoordinator:
    """Minimal base class, enough to instantiate the coordinator."""

    def __init__(self, hass, logger, name=None, update_interval=None) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True

    def async_update_listeners(self) -> None:
        pass

    async def async_request_refresh(self) -> None:
        pass


class FakeBleakClient:
    """Records what the drivers ask of a BLE connection."""

    def __init__(self, device=None, **kwargs) -> None:
        self.device = device
        self.is_connected = True
        self.notify_handler = None
        self.writes: list[bytes] = []

    async def start_notify(self, _char, handler) -> None:
        self.notify_handler = handler

    async def write_gatt_char(self, _char, data, response=False) -> None:
        self.writes.append(bytes(data))

    async def disconnect(self) -> None:
        self.is_connected = False


class BleakError(Exception):
    pass


#: Every establish_connection() call made through the stub.
establish_connection_calls: list[dict] = []


def _install_stubs() -> None:
    if PACKAGE in sys.modules:
        return

    bleak = types.ModuleType("bleak")
    bleak.BleakClient = FakeBleakClient
    bleak.BleakScanner = types.SimpleNamespace(discover=None)
    bleak_exc = types.ModuleType("bleak.exc")
    bleak_exc.BleakError = BleakError
    bleak.exc = bleak_exc

    async def establish_connection(client_class, device, name, **kwargs):
        establish_connection_calls.append(
            {"client_class": client_class, "device": device, "name": name, **kwargs}
        )
        return client_class(device)

    retry = types.ModuleType("bleak_retry_connector")
    retry.establish_connection = establish_connection

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    bluetooth = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth.async_ble_device_from_address = lambda *args, **kwargs: None
    bluetooth.async_discovered_service_info = lambda *args, **kwargs: []
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed

    homeassistant.components = components
    homeassistant.core = core
    homeassistant.helpers = helpers
    components.bluetooth = bluetooth
    helpers.update_coordinator = update_coordinator

    skyrc_ble = types.ModuleType("skyrc_ble")
    skyrc_ble.Mc3000 = object

    modules = {
        "bleak": bleak,
        "bleak.exc": bleak_exc,
        "bleak_retry_connector": retry,
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.bluetooth": bluetooth,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "skyrc_ble": skyrc_ble,
    }
    sys.modules.update(modules)

    if "custom_components" not in sys.modules:
        namespace = types.ModuleType("custom_components")
        namespace.__path__ = [str(ROOT / "custom_components")]
        sys.modules["custom_components"] = namespace

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(PACKAGE_PATH)]
    sys.modules[PACKAGE] = package


def load(module_name: str):
    """Load one module of the integration, stubbing its imports first."""
    _install_stubs()

    full_name = f"{PACKAGE}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(full_name, PACKAGE_PATH / f"{module_name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module
