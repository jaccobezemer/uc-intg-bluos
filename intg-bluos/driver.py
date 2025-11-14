#!/usr/bin/env python3
"""BluOS Integration Driver for Unfolded Circle Remote 3."""
import asyncio
import logging
import os
import sys
import time
from typing import Any, Optional

import ucapi
from ucapi import MediaPlayer, StatusCodes, IntegrationSetupError, SetupError
from ucapi.media_player import (
    Attributes,
    Commands,
    Features,
    States,
    MediaType,
)

from bluos_client import BluOSClient
from const import STATE_PLAY, STATE_PAUSE, STATE_STOP, STATE_STREAM
from config import Config, BluOSDeviceConfig
from discovery import BluOSDeviceDiscovery

_LOG = logging.getLogger(__name__)

# Configuration - initialized in main()
api: ucapi.IntegrationAPI = None
config: Config = None
discovery: BluOSDeviceDiscovery = None


class BluOSDevice:
    """BluOS Device representation."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize BluOS device.

        Args:
            config: Device configuration with host and port
        """
        self.config = config
        self.entity_id = f"bluos_{config['host'].replace('.', '_')}"
        self.name = config.get("name", "BluOS Player")

        self.client = BluOSClient(
            host=config["host"],
            port=config.get("port", 11000)
        )

        # Current status
        self._state = States.OFF
        self._volume = 50
        self._muted = False
        self._source_list = []
        self._source = None
        self._media_title = None
        self._media_artist = None
        self._media_album = None
        self._media_image_url = None
        self._shuffle = False
        self._repeat = None
        self._presets = []  # Cache for presets
        self._inputs = []  # Cache for inputs
        self._polling = False  # Flag to prevent multiple poll loops
        self._poll_task: Optional[asyncio.Task] = None  # Track the polling task

        # Create the media player entity with command handler
        self.entity = MediaPlayer(
            identifier=self.entity_id,
            name=self.name,
            features=[
                Features.PLAY_PAUSE,
                Features.STOP,
                Features.NEXT,
                Features.PREVIOUS,
                Features.VOLUME,
                Features.VOLUME_UP_DOWN,
                Features.MUTE_TOGGLE,
                Features.MUTE,
                Features.UNMUTE,
                Features.SHUFFLE,
                Features.REPEAT,
                Features.SELECT_SOURCE,
                Features.MEDIA_TITLE,
                Features.MEDIA_ARTIST,
                Features.MEDIA_ALBUM,
            ],
            attributes={
                Attributes.STATE: self._state,
                Attributes.VOLUME: self._volume,
                Attributes.MUTED: self._muted,
                Attributes.SOURCE: self._source,
                Attributes.SOURCE_LIST: self._source_list,
                Attributes.SHUFFLE: self._shuffle,
                Attributes.REPEAT: self._repeat,
            },
            device_class="speaker",
            cmd_handler=self.handle_command,
        )

    async def connect(self) -> bool:
        """Connect to BluOS device."""
        _LOG.info(f"Connecting to BluOS device at {self.config['host']}:{self.config.get('port', 11000)}")

        try:
            # Fetch initial status
            _LOG.debug(f"Fetching status from {self.client.base_url}/Status")
            status = await self.client.get_status()

            if status is None:
                _LOG.error(f"Could not fetch status from BluOS device at {self.client.base_url}")
                return False

            # Update internal state
            await self._update_from_status(status)

            # Fetch presets and inputs
            presets = await self.client.get_presets()
            if presets:
                self._presets = presets
                _LOG.info(f"Found {len(presets)} preset(s): {[p['name'] for p in presets]}")

            inputs = await self.client.get_inputs()
            if inputs:
                self._inputs = inputs
                _LOG.info(f"Found {len(inputs)} input(s): {[i.get('name', 'Unknown') for i in inputs]}")
                _LOG.debug(f"Input details: {inputs}")

            # Merge presets and inputs into source_list
            # Presets take precedence - if a preset and input have the same name, only include preset
            self._source_list = []
            preset_names = set()

            if self._presets:
                preset_names = {p['name'] for p in self._presets if "name" in p}
                self._source_list.extend(preset_names)

            if self._inputs:
                # Only add inputs that don't conflict with preset names
                for input_item in self._inputs:
                    if "name" in input_item and input_item["name"] not in preset_names:
                        self._source_list.append(input_item["name"])

            _LOG.info(f"Total sources available: {self._source_list}")
            if preset_names:
                _LOG.info(f"Preset names: {preset_names}")

            await self.update_attributes()

            # Start long-polling for status updates
            if not self._polling and (self._poll_task is None or self._poll_task.done()):
                self._polling = True
                self._poll_task = asyncio.create_task(self._long_poll_status())
                _LOG.info(f"Started long-polling task for {self.name}")

            return True

        except Exception as e:
            _LOG.error(f"Error connecting: {e}", exc_info=True)
            return False

    async def disconnect(self):
        """Disconnect from device."""
        _LOG.info(f"Disconnecting {self.name}")
        # Stop polling
        self._polling = False

        # Cancel the polling task if it exists
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        self._poll_task = None

        # Close client
        await self.client.close()

    async def _update_from_status(self, status: dict):
        """
        Update interne state van BluOS status.

        Args:
            status: Status dictionary van BluOS client
        """
        # Volume en mute
        if "volume" in status:
            self._volume = status["volume"]

        if "muted" in status:
            self._muted = status["muted"]

        # Playback state
        if "state" in status:
            bluos_state = status["state"]
            if bluos_state in [STATE_PLAY, STATE_STREAM]:
                self._state = States.PLAYING
            elif bluos_state == STATE_PAUSE:
                self._state = States.PAUSED
            elif bluos_state == STATE_STOP:
                self._state = States.ON
            else:
                self._state = States.ON

        # Source/service - check this FIRST before setting media info
        if "service" in status:
            self._source = status["service"]

        # Media info - clear for Capture sources, update for others
        if self._source and "Capture" in self._source:
            # Capture inputs don't have meaningful media info
            self._media_title = None
            self._media_artist = None
            self._media_album = None

            # Use currentImage for Capture inputs (e.g., TV icon)
            if "currentImage" in status and status["currentImage"]:
                image_url = status["currentImage"]
                if image_url.startswith("/"):
                    image_url = f"{self.client.base_url}{image_url}"
                self._media_image_url = image_url
            else:
                self._media_image_url = None
        else:
            # Regular sources (Spotify, TuneIn, etc.) have media info
            self._media_title = status.get("title")
            self._media_artist = status.get("artist")
            self._media_album = status.get("album")

            # Media image URL - convert relative URLs to absolute
            if "image" in status and status["image"]:
                image_url = status["image"]
                if image_url.startswith("/"):
                    image_url = f"{self.client.base_url}{image_url}"
                self._media_image_url = image_url
            else:
                self._media_image_url = None

        # Shuffle en repeat
        if "shuffle" in status:
            self._shuffle = status["shuffle"]

        if "repeat" in status:
            repeat_mode = status["repeat"]
            if repeat_mode == 0:
                self._repeat = "ALL"
            elif repeat_mode == 1:
                self._repeat = "ONE"
            else:
                self._repeat = "OFF"

    async def update_attributes(self):
        """Update entity attributes."""
        attributes = {
            Attributes.STATE: self._state,
            Attributes.VOLUME: self._volume,
            Attributes.MUTED: self._muted,
            Attributes.SOURCE: self._source,
            Attributes.SOURCE_LIST: self._source_list,
            Attributes.SHUFFLE: self._shuffle,
            Attributes.REPEAT: self._repeat,
        }

        # Always set title/artist/album (even if None) to properly clear old streaming info from UI
        attributes[Attributes.MEDIA_TITLE] = self._media_title
        attributes[Attributes.MEDIA_ARTIST] = self._media_artist
        attributes[Attributes.MEDIA_ALBUM] = self._media_album

        # Only set image if available (don't clear Capture input icons)
        if self._media_image_url:
            attributes[Attributes.MEDIA_IMAGE_URL] = self._media_image_url

        api.configured_entities.update_attributes(
            self.entity_id,
            attributes
        )

    async def _long_poll_status(self):
        """
        Long-poll BluOS status using /Status endpoint.

        Uses /Status (not /SyncStatus) because we need full playback status.
        This provides real-time updates for playback, volume, and hardware changes.
        """
        _LOG.info(f"Starting long-polling for {self.name}")
        last_request_time = time.monotonic()
        consecutive_errors = 0
        max_consecutive_errors = 5

        try:
            while self._polling:
                try:
                    # Ensure minimum 1 second between requests (BluOS requirement)
                    elapsed = time.monotonic() - last_request_time
                    if elapsed < 1.0:
                        await asyncio.sleep(1.0 - elapsed)

                    last_request_time = time.monotonic()

                    # Use long-polling with 100 second timeout on /Status endpoint
                    # This captures ALL changes: playback, volume, hardware buttons, etc.
                    status = await self.client.sync_status(timeout=100)

                    if status:
                        # Got status update (including hardware volume changes!)
                        await self._update_from_status(status)
                        await self.update_attributes()
                        _LOG.debug(f"Long-poll update received for {self.name}")
                        consecutive_errors = 0  # Reset error counter
                    else:
                        # Timeout or error - wait briefly before retry
                        consecutive_errors += 1
                        if consecutive_errors >= max_consecutive_errors:
                            _LOG.error(f"Too many consecutive errors ({consecutive_errors}), stopping long-poll for {self.name}")
                            break
                        _LOG.debug(f"Long-poll timeout/error for {self.name}, waiting 5s before retry (error {consecutive_errors}/{max_consecutive_errors})")
                        await asyncio.sleep(5)

                except asyncio.CancelledError:
                    _LOG.info(f"Long-polling task cancelled for {self.name}")
                    raise
                except Exception as e:
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        _LOG.error(f"Too many consecutive errors ({consecutive_errors}), stopping long-poll for {self.name}: {e}", exc_info=True)
                        break
                    _LOG.warning(f"Long-poll error for {self.name}: {type(e).__name__}: {e}, waiting 10s before retry (error {consecutive_errors}/{max_consecutive_errors})")
                    await asyncio.sleep(10)
        finally:
            self._polling = False
            _LOG.info(f"Stopped long-polling for {self.name}")

    async def handle_command(self, entity_id: str, command: str, params: dict[str, Any] | None = None) -> StatusCodes:
        """
        Handle media player commands.

        Args:
            entity_id: The ID of the entity receiving the command (ignored)
            command: The command to execute
            params: Optional parameters

        Returns:
            StatusCode of the operation
        """
        _LOG.info(f"Command received: {command} with params: {params}")

        try:
            # Track if we need to fetch full status (for non-volume/mute commands)
            needs_status_update = True

            if command == Commands.PLAY_PAUSE:
                # Toggle between play and pause
                await self.client.pause(toggle=True)

            elif command == Commands.STOP:
                await self.client.stop()

            elif command == Commands.NEXT:
                await self.client.skip()

            elif command == Commands.PREVIOUS:
                await self.client.back()

            elif command == Commands.VOLUME:
                if params and "volume" in params:
                    new_volume = await self.client.set_volume(params["volume"])
                    if new_volume is not None:
                        self._volume = new_volume
                        await self.update_attributes()
                        needs_status_update = False

            elif command == Commands.VOLUME_UP:
                new_volume = await self.client.volume_up()
                if new_volume is not None:
                    self._volume = new_volume
                    await self.update_attributes()
                    needs_status_update = False

            elif command == Commands.VOLUME_DOWN:
                new_volume = await self.client.volume_down()
                if new_volume is not None:
                    self._volume = new_volume
                    await self.update_attributes()
                    needs_status_update = False

            elif command == Commands.MUTE_TOGGLE:
                result = await self.client.toggle_mute()
                if result:
                    if "volume" in result:
                        self._volume = result["volume"]
                    if "muted" in result:
                        self._muted = result["muted"]
                    await self.update_attributes()
                    needs_status_update = False

            elif command == Commands.MUTE:
                result = await self.client.set_mute(True)
                if result:
                    if "volume" in result:
                        self._volume = result["volume"]
                    if "muted" in result:
                        self._muted = result["muted"]
                    await self.update_attributes()
                    needs_status_update = False

            elif command == Commands.UNMUTE:
                result = await self.client.set_mute(False)
                if result:
                    if "volume" in result:
                        self._volume = result["volume"]
                    if "muted" in result:
                        self._muted = result["muted"]
                    await self.update_attributes()
                    needs_status_update = False

            elif command == Commands.SHUFFLE:
                if params and "shuffle" in params:
                    await self.client.set_shuffle(params["shuffle"])

            elif command == Commands.REPEAT:
                if params and "repeat" in params:
                    repeat_mode = params["repeat"]
                    if repeat_mode == "ALL":
                        await self.client.set_repeat(0)
                    elif repeat_mode == "ONE":
                        await self.client.set_repeat(1)
                    else:  # OFF
                        await self.client.set_repeat(2)

            elif command == Commands.SELECT_SOURCE:
                if params and "source" in params:
                    source_name = params["source"]

                    # First check presets (they take precedence)
                    found = False
                    for preset in self._presets:
                        if preset.get("name") == source_name and "id" in preset:
                            await self.client.play_preset(int(preset["id"]))
                            _LOG.info(f"Selected preset: {source_name} (ID: {preset['id']})")
                            found = True
                            break

                    # If not a preset, try inputs (use playURL)
                    if not found:
                        for input_item in self._inputs:
                            if input_item.get("name") == source_name:
                                if "playURL" in input_item:
                                    await self.client.play_input(input_item["playURL"])
                                    _LOG.info(f"Selected input: {source_name} (playURL: {input_item['playURL']})")

                                    # Clear media info for Capture inputs (they don't have artist/title/album)
                                    if "Capture" in input_item.get("playURL", ""):
                                        self._media_title = None
                                        self._media_artist = None
                                        self._media_album = None
                                        _LOG.debug(f"Cleared media info for Capture input: {source_name}")
                                        # Immediately update attributes to clear the UI
                                        await self.update_attributes()
                                        # Don't fetch status afterwards - it would restore old media info
                                        needs_status_update = False

                                    found = True
                                else:
                                    _LOG.error(f"Input '{source_name}' has no playURL")
                                break

                    if not found:
                        _LOG.warning(f"Source not found: {source_name}")

            # Only fetch full status for commands that don't return their own data
            if needs_status_update:
                await asyncio.sleep(0.1)
                status = await self.client.get_status()
                if status:
                    await self._update_from_status(status)
                    await self.update_attributes()
                    _LOG.debug(f"Updated attributes after command {command}")

            return StatusCodes.OK

        except Exception as e:
            _LOG.error(f"Error executing command {command}: {e}")
            return StatusCodes.SERVER_ERROR


# Global device instances - managed by device_id
bluos_devices: dict[str, BluOSDevice] = {}

# Discovery cache - stores discovered device info for setup
discovered_devices: dict[str, dict] = {}


async def add_device(device_config: BluOSDeviceConfig) -> bool:
    """
    Add a BluOS device.

    Args:
        device_config: Device configuration

    Returns:
        True if successful
    """
    global bluos_devices

    _LOG.info(f"Adding BluOS device: {device_config.device_id} ({device_config.name})")

    device = BluOSDevice({
        "host": device_config.host,
        "port": device_config.port,
        "name": device_config.name
    })

    # Connect to BluOS device
    connected = await device.connect()

    if not connected:
        _LOG.error(f"Could not connect to BluOS device {device_config.device_id}")
        return False

    # Store device
    bluos_devices[device_config.device_id] = device

    # Add entity to both collections
    api.available_entities.add(device.entity)
    api.configured_entities.add(device.entity)

    _LOG.info(f"BluOS device added: {device_config.device_id}")
    return True


async def remove_device(device_id: str) -> bool:
    """
    Remove a BluOS device.

    Args:
        device_id: Device identifier

    Returns:
        True if successful
    """
    global bluos_devices

    if device_id not in bluos_devices:
        _LOG.warning(f"Device not found: {device_id}")
        return False

    device = bluos_devices[device_id]

    # Disconnect device
    await device.disconnect()

    # Remove entity
    entity_id = device.entity_id
    api.configured_entities.remove(entity_id)
    api.available_entities.remove(entity_id)

    # Remove from dictionary
    del bluos_devices[device_id]

    _LOG.info(f"BluOS device removed: {device_id}")
    return True


async def on_device_discovered(device_info: dict) -> None:
    """
    Handle discovered BluOS device.

    Stores device info for use during setup.
    Does NOT automatically configure or connect.

    Args:
        device_info: Device info from mDNS discovery
    """
    global discovered_devices

    _LOG.info(f"Device discovered via mDNS: {device_info}")

    # Create device_id
    device_id = f"bluos_{device_info['host'].replace('.', '_')}"

    # Check if already in discovered cache
    if device_id in discovered_devices:
        _LOG.debug(f"Device {device_id} already in discovered cache")
        return

    # Store discovery info for later use in setup
    # Note: We cache even if device is already configured, in case setup runs again
    discovered_devices[device_id] = device_info

    if device_id in bluos_devices:
        _LOG.info(f"Cached discovered device: {device_info['name']} ({device_id}) - already configured")
    else:
        _LOG.info(f"Cached discovered device: {device_info['name']} ({device_id})")


async def main(loop):
    """Start the integration driver."""
    global api, config, discovery

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
    )

    # Set specific loggers to appropriate levels - reduce spam
    logging.getLogger("ucapi").setLevel(logging.ERROR)  # Was WARNING, too verbose
    logging.getLogger("ucapi.api").setLevel(logging.ERROR)
    logging.getLogger("ucapi.entities").setLevel(logging.ERROR)
    logging.getLogger("websockets").setLevel(logging.ERROR)
    logging.getLogger("bluos_client").setLevel(logging.WARNING)  # Was INFO
    logging.getLogger("__main__").setLevel(logging.INFO)

    # Load version from driver.json
    import json
    driver_json_path = os.path.join(os.path.dirname(__file__), "..", "driver.json")
    try:
        with open(driver_json_path, 'r', encoding='utf-8') as f:
            driver_info = json.load(f)
            version = driver_info.get("version", "unknown")
    except Exception as e:
        _LOG.warning(f"Could not load version from driver.json: {e}")
        version = "unknown"

    _LOG.info(f"BluOS Integration starting (v{version})")

    # Use the provided event loop
    api = ucapi.IntegrationAPI(loop)

    # Initialize configuration manager
    config_dir = os.environ.get("UC_CONFIG_HOME", os.path.expanduser("~/.config/uc-bluos"))
    config = Config(config_dir)
    _LOG.info(f"Configuration loaded: {len(config.all_devices())} devices")

    # Initialize discovery
    discovery = BluOSDeviceDiscovery()

    # Event handlers
    @api.listens_to(ucapi.Events.CONNECT)
    async def on_connect() -> None:
        """Handle connect event."""
        _LOG.info("UC Remote 3 connected")
        await api.set_device_state(ucapi.DeviceStates.CONNECTED)

        # Discovery already running from main()
        # Just load previously configured devices
        for device_config in config.enabled_devices():
            # Skip if already loaded
            if device_config.device_id in bluos_devices:
                continue
            _LOG.info(f"Restoring device: {device_config.device_id}")
            await add_device(device_config)

    @api.listens_to(ucapi.Events.DISCONNECT)
    async def on_disconnect() -> None:
        """Handle disconnect event."""
        _LOG.info("UC Remote 3 disconnected")

        # Stop discovery
        if discovery:
            await discovery.stop()

        # Disconnect all devices
        for device in bluos_devices.values():
            await device.disconnect()

    @api.listens_to(ucapi.Events.ENTER_STANDBY)
    async def on_standby() -> None:
        """Handle standby event."""
        _LOG.info("UC Remote 3 entering standby")

    @api.listens_to(ucapi.Events.EXIT_STANDBY)
    async def on_exit_standby() -> None:
        """Handle exit standby event."""
        _LOG.info("UC Remote 3 exiting standby")

    @api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
    async def on_subscribe_entities(entity_ids: list[str]) -> None:
        """Handle entity subscription - fetch status once on-demand."""
        _LOG.info(f"Subscribe entities: {entity_ids}")
        # Fetch current status for subscribed entities (one-time, no polling)
        for entity_id in entity_ids:
            if entity_id in bluos_devices:
                device = bluos_devices[entity_id]
                try:
                    status = await device.client.get_status()
                    if status:
                        await device._update_from_status(status)
                        await device.update_attributes()
                        _LOG.debug(f"Updated status for {entity_id} on subscribe")
                except Exception as e:
                    _LOG.warning(f"Failed to update status for {entity_id}: {e}")

    @api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
    async def on_unsubscribe_entities(entity_ids: list[str]) -> None:
        """Handle entity unsubscribe."""
        _LOG.info(f"Unsubscribe entities: {entity_ids}")

    # Setup handler - multi-step setup with manual or auto-discovery options
    async def handle_driver_setup(msg: ucapi.DriverSetupRequest) -> ucapi.SetupAction:
        """
        Ask user for device details or use auto-discovery.

        Returns:
            RequestUserInput with manual entry fields
        """
        _LOG.info("========== DRIVER SETUP STARTED ==========")

        # Workaround for web-configurator not picking up first response
        await asyncio.sleep(1)

        return ucapi.RequestUserInput(
            title="BluOS Device Setup",
            settings=[
                {
                    "id": "info",
                    "label": {
                        "en": "Manual or Auto-Discovery",
                        "nl": "Handmatig of Auto-Detectie"
                    },
                    "field": {
                        "label": {
                            "value": {
                                "en": "Enter device details below, or leave all fields blank for auto-discovery.",
                                "nl": "Vul apparaat gegevens in, of laat alles leeg voor auto-detectie."
                            }
                        }
                    }
                },
                {
                    "id": "name",
                    "label": {"en": "Device Name", "nl": "Apparaat Naam"},
                    "field": {"text": {"value": ""}}
                },
                {
                    "id": "address",
                    "label": {"en": "IP Address", "nl": "IP Adres"},
                    "field": {"text": {"value": ""}}
                },
                {
                    "id": "port",
                    "label": {"en": "Port", "nl": "Poort"},
                    "field": {"number": {"value": 11000, "min": 1, "max": 65535}}
                }
            ]
        )

    async def handle_user_data_response(msg: ucapi.UserDataResponse) -> ucapi.SetupAction:
        """
        Process user input - either manual config or start discovery.

        Args:
            msg: User data response with input values

        Returns:
            SetupAction (either RequestUserInput for device selection or SetupComplete)
        """
        global discovered_devices

        _LOG.info("Processing user data response")
        _LOG.debug(f"Input values: {msg.input_values}")

        # Step 2: User selected discovered device
        if "device_choice" in msg.input_values:
            choice = msg.input_values["device_choice"]
            _LOG.info(f"User selected discovered device: {choice}")

            if choice not in discovered_devices:
                _LOG.error(f"Selected device {choice} not found")
                return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.NOT_FOUND)

            device_info = discovered_devices[choice]
            name = device_info.get("name", "BluOS Device")
            host = device_info["host"]
            port = device_info.get("port", 11000)

            return await configure_device(name, host, port)

        # Step 1: Manual entry or discovery
        elif "address" in msg.input_values:
            name = msg.input_values.get("name", "").strip()
            address = msg.input_values.get("address", "").strip()
            port = msg.input_values.get("port", 11000)

            # Manual configuration - name and address both provided
            if name and address:
                _LOG.info(f"Manual configuration: {name} at {address}:{port}")
                return await configure_device(name, address, port)

            # Auto-discovery - all fields empty
            elif not name and not address:
                _LOG.info("Starting auto-discovery...")
                discovered_devices.clear()

                # Start discovery
                await discovery.start(on_device_discovered)
                await asyncio.sleep(8)  # Wait for discovery
                await discovery.stop()

                if not discovered_devices:
                    _LOG.warning("No BluOS devices discovered")
                    return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.NOT_FOUND)

                # Build dropdown with discovered devices
                dropdown_items = []
                for device_id, device_info in discovered_devices.items():
                    dropdown_items.append({
                        "id": device_id,
                        "label": {"en": f"{device_info.get('name', 'BluOS')} ({device_info['host']})"}
                    })

                _LOG.info(f"Found {len(dropdown_items)} device(s)")

                return ucapi.RequestUserInput(
                    title="Select BluOS Device",
                    settings=[{
                        "id": "device_choice",
                        "label": {"en": "Device", "nl": "Apparaat"},
                        "field": {
                            "dropdown": {
                                "value": dropdown_items[0]["id"],
                                "items": dropdown_items
                            }
                        }
                    }]
                )

            # Incomplete input - need both name and address for manual config
            else:
                _LOG.error("Incomplete manual configuration - need both name and address")
                return ucapi.SetupError(
                    error_type=ucapi.IntegrationSetupError.OTHER,
                    error_message="Please provide both device name and IP address for manual configuration"
                )

        _LOG.error("Invalid user data response")
        return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.OTHER)

    async def configure_device(name: str, host: str, port: int) -> ucapi.SetupAction:
        """
        Configure and add a BluOS device.

        Args:
            name: Device name
            host: Device IP address
            port: Device port

        Returns:
            SetupComplete on success or SetupError on failure
        """
        _LOG.info(f"Configuring device: {name} at {host}:{port}")

        device_id = f"bluos_{host.replace('.', '_')}"

        # Check if already configured
        if device_id in bluos_devices:
            _LOG.info(f"Device {device_id} already configured")
            return ucapi.SetupComplete()

        # Create device config
        device_config = BluOSDeviceConfig(
            device_id=device_id,
            name=name,
            host=host,
            port=port
        )

        # Save to config
        config.add_device(device_config)

        # Add device and connect
        success = await add_device(device_config)

        if not success:
            _LOG.error(f"Failed to connect to device at {host}:{port}")
            return ucapi.SetupError(error_type=ucapi.IntegrationSetupError.CONNECTION_REFUSED)

        _LOG.info(f"Successfully configured device: {name}")
        return ucapi.SetupComplete()

    # Dispatcher for setup messages
    async def driver_setup_handler(msg: ucapi.SetupDriver) -> ucapi.SetupAction:
        """
        Dispatch driver setup requests to corresponding handlers.

        Args:
            msg: the setup driver request object, either DriverSetupRequest or UserDataResponse

        Returns:
            the setup action on how to continue
        """
        if isinstance(msg, ucapi.DriverSetupRequest):
            return await handle_driver_setup(msg)
        if isinstance(msg, ucapi.UserDataResponse):
            return await handle_user_data_response(msg)

        _LOG.error(f"Unknown setup message type: {type(msg)}")
        return ucapi.SetupError()

    # Start the integration API with setup handler
    _LOG.info("Calling api.init() with setup handler...")
    await api.init("driver.json", driver_setup_handler)
    _LOG.info("API.INIT completed - driver ready")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(main(loop))
    loop.run_forever()
