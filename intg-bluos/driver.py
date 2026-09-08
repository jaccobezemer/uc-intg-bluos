#!/usr/bin/env python3
"""BluOS Integration Driver for Unfolded Circle Remote 3."""
import asyncio
import json
import logging
import os
import sys

from ucapi import DeviceStates
from ucapi_framework import BaseConfigManager, BaseIntegrationDriver, get_config_path

from config import BluOSDeviceConfig
from device import BluOSDevice
from discovery import BluOSDiscovery
from media_player import BluOSMediaPlayer
from setup_flow import BluOSSetupFlow

_LOG = logging.getLogger(__name__)


class BluOSDriver(BaseIntegrationDriver[BluOSDevice, BluOSDeviceConfig]):
    """BluOS integration driver."""

    def __init__(self):
        super().__init__(
            device_class=BluOSDevice,
            entity_classes=[BluOSMediaPlayer],
            driver_id="bluos",
            require_connection_before_registry=True,
        )

    def device_from_entity_id(self, entity_id: str) -> str | None:
        # The Remote may still hold a stale entity_id from before 0.2.5
        # (e.g. "bluos_<host>", no separator). Returning None here lets the
        # framework's own "unknown entity" handling skip it gracefully
        # instead of raising and crashing the subscribe-entities callback.
        if self.entity_id_separator not in entity_id:
            _LOG.warning("Ignoring unrecognized (legacy?) entity_id: %s", entity_id)
            return None
        return super().device_from_entity_id(entity_id)


def _get_driver_path() -> str:
    """Get the path to driver.json, handling both source and PyInstaller bundles."""
    candidates = [
        # Current working directory (UC Remote sets cwd to the package root)
        "driver.json",
        # Relative to executable's parent (package_root/bin/driver -> package_root/driver.json)
        os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "driver.json"),
        # Same directory as the executable
        os.path.join(os.path.dirname(sys.executable), "driver.json"),
        # Relative to this source file (running from source, not frozen)
        os.path.join(os.path.dirname(__file__), "..", "driver.json"),
    ]

    for path in candidates:
        if os.path.isfile(path):
            _LOG.info("Found driver.json at: %s", path)
            return path

    _LOG.warning("driver.json not found in any expected location, using fallback")
    return "driver.json"


def _get_version(driver_json_path: str) -> str:
    try:
        with open(driver_json_path, "r", encoding="utf-8") as f:
            return json.load(f).get("version", "unknown")
    except (OSError, json.JSONDecodeError) as err:
        _LOG.warning("Could not load version from driver.json: %s", err)
        return "unknown"


def _migrate_legacy_config(config_dir: str) -> None:
    """Convert the pre-0.2.5 "devices.json" config format to "config.json".

    Versions before 0.2.5 stored devices in "devices.json" via a hand-rolled
    Config class. ucapi_framework's BaseConfigManager looks for "config.json",
    so without this one-time conversion an upgrade would silently lose the
    configured device (and its entity) instead of just renaming it.

    This writes the new file directly, before BaseConfigManager is constructed,
    so the device is picked up by its normal load() and goes through the
    normal single registration path in register_all_device_instances() -
    calling config_manager.add_or_update() here instead would trigger
    on_device_added() immediately (a second, racing registration).
    """
    legacy_path = os.path.join(config_dir, "devices.json")
    new_path = os.path.join(config_dir, "config.json")

    if not os.path.isfile(legacy_path) or os.path.isfile(new_path):
        return

    try:
        with open(legacy_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as err:
        _LOG.warning("Could not read legacy config %s: %s", legacy_path, err)
        return

    devices = []
    for entry in data.get("devices", []):
        host = entry.get("host")
        if not host:
            continue
        devices.append(
            {
                "identifier": host.replace(".", "_"),
                "name": entry.get("name", "BluOS Player"),
                "host": host,
                "port": entry.get("port", 11000),
            }
        )

    if not devices:
        return

    try:
        os.makedirs(config_dir, exist_ok=True)
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(devices, f, ensure_ascii=False)
        _LOG.info("Migrated %d device(s) from legacy devices.json to config.json", len(devices))
    except OSError as err:
        _LOG.warning("Could not write migrated config %s: %s", new_path, err)


async def main():
    """Start the integration driver."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
    )
    logging.getLogger("ucapi").setLevel(logging.ERROR)
    logging.getLogger("ucapi_framework").setLevel(logging.INFO)
    logging.getLogger("websockets").setLevel(logging.ERROR)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    driver_json_path = _get_driver_path()
    _LOG.info("BluOS Integration starting (v%s)", _get_version(driver_json_path))

    driver = BluOSDriver()

    config_path = get_config_path(driver.api.config_dir_path or "")
    _migrate_legacy_config(config_path)
    driver.config_manager = BaseConfigManager(
        config_path,
        add_handler=driver.on_device_added,
        remove_handler=driver.on_device_removed,
        config_class=BluOSDeviceConfig,
    )

    discovery = BluOSDiscovery()
    setup_handler = BluOSSetupFlow.create_handler(driver, discovery=discovery)

    await driver.api.init(driver_json_path, setup_handler)
    await driver.register_all_device_instances(connect=False)

    device_count = len(list(driver.config_manager.all()))
    await driver.api.set_device_state(
        DeviceStates.CONNECTED if device_count > 0 else DeviceStates.DISCONNECTED
    )
    _LOG.info("BluOS integration started - %d device(s) configured", device_count)

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
