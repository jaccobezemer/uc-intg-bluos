"""BluOS device implementation using ucapi_framework's PollingDevice."""
import logging
from typing import Any, Optional

from ucapi_framework import PollingDevice

from bluos_client import BluOSClient
from config import BluOSDeviceConfig
from const import STATE_PAUSE, STATE_PLAY, STATE_STREAM

_LOG = logging.getLogger(__name__)

# BluOS long-polls /Status itself (up to 100s); this is just the minimum gap
# between poll cycles so we don't hammer the device on repeated fast changes.
POLL_INTERVAL = 1


class BluOSDevice(PollingDevice):
    """BluOS player wrapped as a framework PollingDevice."""

    def __init__(self, device_config: BluOSDeviceConfig, **kwargs: Any) -> None:
        super().__init__(device_config, poll_interval=POLL_INTERVAL, **kwargs)
        self._device_config = device_config
        self.client = BluOSClient(host=device_config.host, port=device_config.port)

        self._state: str = "UNAVAILABLE"
        self._volume: int = 50
        self._muted: bool = False
        self._shuffle: bool = False
        self._repeat: Optional[str] = None
        self._title: Optional[str] = None
        self._artist: Optional[str] = None
        self._album: Optional[str] = None
        self._image_url: Optional[str] = None
        self._source: Optional[str] = None
        self._source_list: list[str] = []

        self._presets: list[dict] = []
        self._inputs: list[dict] = []

    # -- Identity ---------------------------------------------------------

    @property
    def identifier(self) -> str:
        return self._device_config.identifier

    @property
    def name(self) -> str:
        return self._device_config.name

    @property
    def address(self) -> str:
        return self._device_config.host

    @property
    def log_id(self) -> str:
        return f"{self.name} ({self.address})"

    # -- State ------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def repeat(self) -> Optional[str]:
        return self._repeat

    @property
    def title(self) -> Optional[str]:
        return self._title

    @property
    def artist(self) -> Optional[str]:
        return self._artist

    @property
    def album(self) -> Optional[str]:
        return self._album

    @property
    def image_url(self) -> Optional[str]:
        return self._image_url

    @property
    def source(self) -> Optional[str]:
        return self._source

    @property
    def source_list(self) -> list[str]:
        return self._source_list

    @property
    def presets(self) -> list[dict]:
        return self._presets

    @property
    def inputs(self) -> list[dict]:
        return self._inputs

    # -- Connection ---------------------------------------------------------

    async def establish_connection(self) -> None:
        _LOG.info("[%s] Connecting to BluOS device", self.log_id)

        status = await self.client.get_status()
        if status is None:
            raise ConnectionError(
                f"Cannot reach BluOS device at {self.address}:{self._device_config.port}"
            )

        self._apply_status(status)

        self._presets = await self.client.get_presets() or []
        self._inputs = await self.client.get_inputs() or []
        self._rebuild_source_list()

        self._state = "ON" if self._state == "UNAVAILABLE" else self._state
        _LOG.info(
            "[%s] Connected: %d preset(s), %d input(s)",
            self.log_id,
            len(self._presets),
            len(self._inputs),
        )

    async def disconnect(self) -> None:
        await super().disconnect()
        await self.client.close()
        self._state = "UNAVAILABLE"

    # -- Polling ------------------------------------------------------------

    async def poll_device(self) -> None:
        try:
            status = await self.client.sync_status(timeout=100)
        except Exception as err:  # pylint: disable=broad-except
            _LOG.warning("[%s] Long-poll error: %s", self.log_id, err)
            return

        if status is None:
            return

        self._apply_status(status)
        self.push_update()

    async def refresh_status(self) -> None:
        """Fetch current status immediately and push an update.

        Used after commands so the UI reflects the change without waiting for
        the next long-poll cycle (which can take up to 100s).
        """
        status = await self.client.get_status()
        if status:
            self._apply_status(status)
            self.push_update()

    def _rebuild_source_list(self) -> None:
        # Presets take precedence over inputs with the same name.
        preset_names = {p["name"] for p in self._presets if "name" in p}
        sources = list(preset_names)
        for input_item in self._inputs:
            input_name = input_item.get("name")
            if input_name and input_name not in preset_names:
                sources.append(input_name)
        self._source_list = sources

    def _apply_status(self, status: dict) -> None:
        if "volume" in status:
            self._volume = status["volume"]

        if "muted" in status:
            self._muted = status["muted"]

        if "state" in status:
            bluos_state = status["state"]
            if bluos_state in (STATE_PLAY, STATE_STREAM):
                self._state = "PLAYING"
            elif bluos_state == STATE_PAUSE:
                self._state = "PAUSED"
            else:
                self._state = "ON"

        if "service" in status:
            self._source = status["service"]

        # Capture inputs (e.g. TV) don't carry track metadata - use currentImage instead.
        if self._source and "Capture" in self._source:
            self._title = None
            self._artist = None
            self._album = None
            image = status.get("currentImage")
        else:
            self._title = status.get("title")
            self._artist = status.get("artist")
            self._album = status.get("album")
            image = status.get("image")

        if image:
            self._image_url = f"{self.client.base_url}{image}" if image.startswith("/") else image
        else:
            self._image_url = None

        if "shuffle" in status:
            self._shuffle = status["shuffle"]

        if "repeat" in status:
            self._repeat = {0: "ALL", 1: "ONE"}.get(status["repeat"], "OFF")

    # -- Commands -------------------------------------------------------------

    async def play_pause(self) -> bool:
        return await self.client.pause(toggle=True)

    async def stop(self) -> bool:
        return await self.client.stop()

    async def next_track(self) -> bool:
        return await self.client.skip()

    async def previous_track(self) -> bool:
        return await self.client.back()

    async def set_volume(self, level: int) -> bool:
        return await self.client.set_volume(level) is not None

    async def volume_up(self) -> bool:
        return await self.client.volume_up() is not None

    async def volume_down(self) -> bool:
        return await self.client.volume_down() is not None

    async def set_mute(self, muted: bool) -> bool:
        return await self.client.set_mute(muted) is not None

    async def mute_toggle(self) -> bool:
        return await self.client.toggle_mute() is not None

    async def set_shuffle(self, enabled: bool) -> bool:
        return await self.client.set_shuffle(enabled)

    async def set_repeat(self, mode: str) -> bool:
        value = {"ALL": 0, "ONE": 1}.get(mode, 2)
        return await self.client.set_repeat(value)

    async def select_source(self, source_name: str) -> bool:
        for preset in self._presets:
            if preset.get("name") == source_name and "id" in preset:
                _LOG.info("[%s] Selecting preset: %s", self.log_id, source_name)
                return await self.client.play_preset(int(preset["id"]))

        for input_item in self._inputs:
            if input_item.get("name") == source_name and "playURL" in input_item:
                _LOG.info("[%s] Selecting input: %s", self.log_id, source_name)
                return await self.client.play_input(input_item["playURL"])

        _LOG.warning("[%s] Source not found: %s", self.log_id, source_name)
        return False
