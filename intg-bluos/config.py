"""Configuration for BluOS devices, backed by ucapi_framework's BaseConfigManager."""
from dataclasses import dataclass


@dataclass
class BluOSDeviceConfig:
    """BluOS device configuration."""

    identifier: str = ""
    name: str = ""
    host: str = ""
    port: int = 11000
