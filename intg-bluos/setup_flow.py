"""BluOS setup flow for Unfolded Circle integration."""
import logging
from typing import Any

from ucapi import RequestUserInput
from ucapi_framework import (
    BaseSetupFlow,
    DiscoveredDevice,
    EntityMigrationMapping,
    MigrationData,
)

from bluos_client import BluOSClient
from config import BluOSDeviceConfig

_LOG = logging.getLogger(__name__)

DEFAULT_PORT = 11000

# Versions up to and including 0.2.5 used a "bluos_<host>" entity ID (hand-rolled
# driver). 0.2.5 itself introduced ucapi_framework but its config file mismatch
# (devices.json vs config.json) meant migration never actually ran for anyone on
# that release, so 0.2.5 is treated as still needing it. Fixed in 0.2.6.
_MIGRATION_NEEDED_UP_TO = (0, 2, 5)


class BluOSSetupFlow(BaseSetupFlow[BluOSDeviceConfig]):
    """Setup flow for BluOS integration: auto-discovery with manual fallback."""

    def get_manual_entry_form(self) -> RequestUserInput:
        return RequestUserInput(
            {"en": "BluOS Device Setup", "nl": "BluOS Apparaat Instellen"},
            [
                {
                    "id": "name",
                    "label": {"en": "Device Name", "nl": "Apparaat Naam"},
                    "field": {"text": {"value": "BluOS Player"}},
                },
                {
                    "id": "host",
                    "label": {"en": "IP Address", "nl": "IP Adres"},
                    "field": {"text": {"value": ""}},
                },
                {
                    "id": "port",
                    "label": {"en": "Port", "nl": "Poort"},
                    "field": {"number": {"value": DEFAULT_PORT, "min": 1, "max": 65535}},
                },
            ],
        )

    async def prepare_input_from_discovery(
        self, discovered: DiscoveredDevice, additional_input: dict[str, Any]
    ) -> dict[str, Any]:
        port = (discovered.extra_data or {}).get("port", DEFAULT_PORT)
        return {
            "name": discovered.name,
            "host": discovered.address,
            "port": port,
        }

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> BluOSDeviceConfig | RequestUserInput:
        host = input_values.get("host", "").strip()
        if not host:
            raise ValueError("IP address is required")

        name = input_values.get("name", "").strip() or f"BluOS Player ({host})"

        port = input_values.get("port", DEFAULT_PORT)
        if isinstance(port, str):
            try:
                port = int(port)
            except ValueError:
                port = DEFAULT_PORT

        _LOG.info("Verifying BluOS device at %s:%d", host, port)

        client = BluOSClient(host=host, port=port)
        try:
            status = await client.get_status()
        finally:
            await client.close()

        if status is None:
            raise ValueError(
                f"Could not reach a BluOS device at {host}:{port}. "
                "Please verify the device is powered on and reachable."
            )

        return BluOSDeviceConfig(
            identifier=host.replace(".", "_"),
            name=name,
            host=host,
            port=port,
        )

    async def is_migration_required(self, previous_version: str) -> bool:
        try:
            parts = tuple(int(p) for p in previous_version.split(".")[:3])
        except (ValueError, AttributeError, TypeError):
            # Unknown/unparsable previous version: migrate to be safe rather
            # than silently orphan existing activity references.
            return True
        return parts <= _MIGRATION_NEEDED_UP_TO

    async def get_migration_data(
        self, previous_version: str, current_version: str
    ) -> MigrationData:
        entity_mappings: list[EntityMigrationMapping] = [
            {
                "previous_entity_id": f"bluos_{device.identifier}",
                "new_entity_id": f"media_player.{device.identifier}",
            }
            for device in self.config.all()
        ]
        return MigrationData(
            previous_driver_id="bluos",
            new_driver_id="bluos",
            entity_mappings=entity_mappings,
        )
