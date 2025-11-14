"""Configuration management for BluOS devices."""
import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional

_LOG = logging.getLogger(__name__)


@dataclass
class BluOSDeviceConfig:
    """BluOS device configuration."""

    device_id: str
    name: str
    host: str
    port: int = 11000
    enabled: bool = True


class Config:
    """Configuration manager for BluOS devices."""

    def __init__(self, config_dir: str):
        """
        Initialize configuration manager.

        Args:
            config_dir: Directory to store configuration
        """
        self.config_dir = config_dir
        self.config_file = os.path.join(config_dir, "devices.json")
        self._devices: dict[str, BluOSDeviceConfig] = {}
        self._load()

    def _load(self):
        """Load configuration from file."""
        if not os.path.exists(self.config_file):
            _LOG.info("No configuration file found, starting fresh")
            return

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for device_data in data.get('devices', []):
                device = BluOSDeviceConfig(**device_data)
                self._devices[device.device_id] = device

            _LOG.info(f"Loaded {len(self._devices)} devices from configuration")
        except Exception as e:
            _LOG.error(f"Error loading configuration: {e}", exc_info=True)

    def _save(self):
        """Save configuration to file."""
        try:
            # Ensure config directory exists
            os.makedirs(self.config_dir, exist_ok=True)

            data = {
                'devices': [asdict(device) for device in self._devices.values()]
            }

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            _LOG.info(f"Saved {len(self._devices)} devices to configuration")
        except Exception as e:
            _LOG.error(f"Error saving configuration: {e}", exc_info=True)

    def add_device(self, device: BluOSDeviceConfig) -> None:
        """Add or update a device."""
        self._devices[device.device_id] = device
        self._save()
        _LOG.info(f"Added device: {device.device_id} ({device.name})")

    def remove_device(self, device_id: str) -> bool:
        """Remove a device."""
        if device_id in self._devices:
            del self._devices[device_id]
            self._save()
            _LOG.info(f"Removed device: {device_id}")
            return True
        return False

    def get_device(self, device_id: str) -> Optional[BluOSDeviceConfig]:
        """Get device configuration."""
        return self._devices.get(device_id)

    def all_devices(self) -> list[BluOSDeviceConfig]:
        """Get all configured devices."""
        return list(self._devices.values())

    def enabled_devices(self) -> list[BluOSDeviceConfig]:
        """Get all enabled devices."""
        return [d for d in self._devices.values() if d.enabled]
