"""BluOS HTTP API Client."""
import asyncio
import logging
from typing import Optional
import xml.etree.ElementTree as ET
import aiohttp

_LOG = logging.getLogger(__name__)


class BluOSClient:
    """Client for communication with BluOS devices via HTTP REST API."""

    def __init__(self, host: str, port: int = 11000):
        """
        Initialize the BluOS client.

        Args:
            host: IP address of the BluOS device
            port: HTTP port (default 11000)
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._session: Optional[aiohttp.ClientSession] = None
        self._etag: Optional[str] = None

    async def _ensure_session(self):
        """Ensure there is an active HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, endpoint: str, params: Optional[dict] = None, timeout: float = 5.0) -> Optional[ET.Element]:
        """
        Execute an HTTP GET request to the BluOS API.

        Args:
            endpoint: API endpoint (e.g. "/Status")
            params: Query parameters
            timeout: Request timeout in seconds (default 5.0)

        Returns:
            XML Element tree of the response, or None on error
        """
        await self._ensure_session()

        url = f"{self.base_url}{endpoint}"
        _LOG.debug(f"Request URL: {url}, params: {params}")

        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                _LOG.debug(f"Actual request URL: {response.url}")
                if response.status == 200:
                    text = await response.text()
                    _LOG.debug(f"Response from {endpoint}: {text[:200]}")

                    try:
                        root = ET.fromstring(text)
                        return root
                    except ET.ParseError as e:
                        _LOG.error(f"XML parse error: {e}")
                        return None
                else:
                    _LOG.error(f"HTTP error {response.status} for {endpoint}")
                    return None

        except asyncio.TimeoutError:
            _LOG.error(f"Timeout on request to {endpoint}")
            return None
        except aiohttp.ClientError as e:
            _LOG.error(f"HTTP client error connecting to {url}: {type(e).__name__}: {e}")
            return None
        except Exception as e:
            _LOG.error(f"Unexpected error during request: {e}")
            return None

    async def get_status(self) -> Optional[dict]:
        """
        Get the current status of the BluOS device.

        Returns:
            Dictionary with status information
        """
        root = await self._request("/Status")

        if root is None:
            return None

        # Parse the most important status fields
        status = {}

        # Volume (0-100)
        volume_elem = root.find("volume")
        if volume_elem is not None and volume_elem.text:
            status["volume"] = int(volume_elem.text)

        # Mute status
        mute_elem = root.find("mute")
        if mute_elem is not None and mute_elem.text:
            status["muted"] = mute_elem.text == "1"

        # Playback state
        state_elem = root.find("state")
        if state_elem is not None and state_elem.text:
            status["state"] = state_elem.text  # play, pause, stop, stream

        # Current track info
        title_elem = root.find("title1")
        if title_elem is not None and title_elem.text:
            status["title"] = title_elem.text

        artist_elem = root.find("artist")
        if artist_elem is not None and artist_elem.text:
            status["artist"] = artist_elem.text

        album_elem = root.find("album")
        if album_elem is not None and album_elem.text:
            status["album"] = album_elem.text

        # Album art / image URL
        image_elem = root.find("image")
        if image_elem is not None and image_elem.text:
            status["image"] = image_elem.text

        # Current image (used by Capture inputs like TV)
        current_image_elem = root.find("currentImage")
        if current_image_elem is not None and current_image_elem.text:
            status["currentImage"] = current_image_elem.text

        # Service/input
        service_elem = root.find("service")
        if service_elem is not None and service_elem.text:
            status["service"] = service_elem.text

        # Shuffle and repeat
        shuffle_elem = root.find("shuffle")
        if shuffle_elem is not None and shuffle_elem.text:
            status["shuffle"] = shuffle_elem.text == "1"

        repeat_elem = root.find("repeat")
        if repeat_elem is not None and repeat_elem.text:
            status["repeat"] = int(repeat_elem.text)  # 0=all, 1=track, 2=off

        # ETag for long-polling
        etag_elem = root.find("etag")
        if etag_elem is not None and etag_elem.text:
            self._etag = etag_elem.text
            status["etag"] = etag_elem.text

        _LOG.debug(f"Status: {status}")
        return status

    async def sync_status(self, timeout: int = 100) -> Optional[dict]:
        """
        Long-polling status check using /Status endpoint - waits until status changes.

        This uses /Status (not /SyncStatus) because we need playback status info.
        /SyncStatus only provides name, volume and grouping - not playback state.

        Args:
            timeout: Timeout in seconds (default 100)

        Returns:
            Dictionary with status information when there is a change, or None on timeout/error
        """
        params = {"timeout": timeout}
        if self._etag:
            params["etag"] = self._etag

        # Use /Status for long-polling (includes playback state)
        # Use longer HTTP timeout for long-polling (timeout + 10 seconds buffer)
        root = await self._request("/Status", params, timeout=float(timeout + 10))
        if root is None:
            return None

        # Parse the status directly (no need for separate get_status call)
        status = {}

        # Volume (0-100)
        volume_elem = root.find("volume")
        if volume_elem is not None and volume_elem.text:
            status["volume"] = int(volume_elem.text)

        # Mute status
        mute_elem = root.find("mute")
        if mute_elem is not None and mute_elem.text:
            status["muted"] = mute_elem.text == "1"

        # Playback state
        state_elem = root.find("state")
        if state_elem is not None and state_elem.text:
            status["state"] = state_elem.text

        # Current track info
        title_elem = root.find("title1")
        if title_elem is not None and title_elem.text:
            status["title"] = title_elem.text

        artist_elem = root.find("artist")
        if artist_elem is not None and artist_elem.text:
            status["artist"] = artist_elem.text

        album_elem = root.find("album")
        if album_elem is not None and album_elem.text:
            status["album"] = album_elem.text

        # Album art / image URL
        image_elem = root.find("image")
        if image_elem is not None and image_elem.text:
            status["image"] = image_elem.text

        # Current image (used by Capture inputs like TV)
        current_image_elem = root.find("currentImage")
        if current_image_elem is not None and current_image_elem.text:
            status["currentImage"] = current_image_elem.text

        # Service/input
        service_elem = root.find("service")
        if service_elem is not None and service_elem.text:
            status["service"] = service_elem.text

        # Shuffle and repeat
        shuffle_elem = root.find("shuffle")
        if shuffle_elem is not None and shuffle_elem.text:
            status["shuffle"] = shuffle_elem.text == "1"

        repeat_elem = root.find("repeat")
        if repeat_elem is not None and repeat_elem.text:
            status["repeat"] = int(repeat_elem.text)

        # ETag for long-polling - IMPORTANT!
        etag_elem = root.find("etag")
        if etag_elem is not None and etag_elem.text:
            self._etag = etag_elem.text
            status["etag"] = etag_elem.text

        return status if status else None

    async def set_volume(self, level: int) -> Optional[int]:
        """
        Set volume (0-100).

        Args:
            level: Volume level (0-100)

        Returns:
            New volume level, or None on error
        """
        level = max(0, min(100, level))
        root = await self._request("/Volume", {"level": level})
        if root is not None:
            volume_elem = root.find("volume")
            if volume_elem is not None and volume_elem.text:
                return int(volume_elem.text)
        return None

    async def volume_up(self) -> Optional[int]:
        """
        Increase volume by 1 step.

        Returns:
            New volume level, or None on error
        """
        root = await self._request("/Volume", {"db": "1"})
        if root is not None:
            volume_elem = root.find("volume")
            if volume_elem is not None and volume_elem.text:
                return int(volume_elem.text)
        return None

    async def volume_down(self) -> Optional[int]:
        """
        Decrease volume by 1 step.

        Returns:
            New volume level, or None on error
        """
        root = await self._request("/Volume", {"db": "-1"})
        if root is not None:
            volume_elem = root.find("volume")
            if volume_elem is not None and volume_elem.text:
                return int(volume_elem.text)
        return None

    async def set_mute(self, muted: bool) -> Optional[dict]:
        """
        Turn mute on or off.

        Args:
            muted: True for mute, False for unmute

        Returns:
            Dict with 'volume' and 'muted' keys, or None on error
        """
        value = "1" if muted else "0"
        root = await self._request("/Volume", {"mute": value})
        if root is not None:
            result = {}
            volume_elem = root.find("volume")
            if volume_elem is not None and volume_elem.text:
                result["volume"] = int(volume_elem.text)
            mute_elem = root.find("mute")
            if mute_elem is not None and mute_elem.text:
                result["muted"] = mute_elem.text == "1"
            return result if result else None
        return None

    async def toggle_mute(self) -> Optional[dict]:
        """
        Toggle mute status.

        Returns:
            Dict with 'volume' and 'muted' keys, or None on error
        """
        root = await self._request("/Volume", {"mute": "toggle"})
        if root is not None:
            result = {}
            volume_elem = root.find("volume")
            if volume_elem is not None and volume_elem.text:
                result["volume"] = int(volume_elem.text)
            mute_elem = root.find("mute")
            if mute_elem is not None and mute_elem.text:
                result["muted"] = mute_elem.text == "1"
            return result if result else None
        return None

    async def play(self) -> bool:
        """Start or resume playback."""
        root = await self._request("/Play")
        return root is not None

    async def pause(self, toggle: bool = False) -> bool:
        """
        Pause playback.

        Args:
            toggle: If True, toggle between play and pause

        Returns:
            True on success
        """
        params = {"toggle": "1"} if toggle else None
        root = await self._request("/Pause", params)
        return root is not None

    async def stop(self) -> bool:
        """Stop playback."""
        root = await self._request("/Stop")
        return root is not None

    async def skip(self) -> bool:
        """Skip to next track."""
        root = await self._request("/Skip")
        return root is not None

    async def back(self) -> bool:
        """Go back to previous track."""
        root = await self._request("/Back")
        return root is not None

    async def set_shuffle(self, enabled: bool) -> bool:
        """
        Turn shuffle on or off.

        Args:
            enabled: True to enable shuffle

        Returns:
            True on success
        """
        value = "1" if enabled else "0"
        root = await self._request("/Shuffle", {"state": value})
        return root is not None

    async def set_repeat(self, mode: int) -> bool:
        """
        Set repeat mode.

        Args:
            mode: 0=repeat all, 1=repeat track, 2=repeat off

        Returns:
            True on success
        """
        mode = max(0, min(2, mode))
        root = await self._request("/Repeat", {"state": mode})
        return root is not None

    async def get_presets(self) -> Optional[list[dict]]:
        """
        Get list of presets (including inputs).

        Returns:
            List of preset dictionaries with id, name, url, image
        """
        root = await self._request("/Presets")

        if root is None:
            return None

        presets = []
        for preset in root.findall("preset"):
            preset_dict = {}

            id_elem = preset.get("id")
            if id_elem:
                preset_dict["id"] = id_elem

            name_elem = preset.get("name")
            if name_elem:
                preset_dict["name"] = name_elem

            url_elem = preset.get("url")
            if url_elem:
                preset_dict["url"] = url_elem

            image_elem = preset.get("image")
            if image_elem:
                preset_dict["image"] = image_elem

            if preset_dict:
                presets.append(preset_dict)

        _LOG.debug(f"Presets: {presets}")
        return presets

    async def play_preset(self, preset_id: int) -> bool:
        """
        Play a preset (including inputs).

        Args:
            preset_id: ID of the preset (1-40)

        Returns:
            True on success
        """
        root = await self._request("/Preset", {"id": preset_id})
        return root is not None

    async def get_inputs(self) -> Optional[list[dict]]:
        """
        Get list of available inputs via Browse.

        Returns:
            List of input dictionaries with name, playURL, image
        """
        root = await self._request("/Browse", {"service": "Capture"})

        if root is None:
            return None

        inputs = []
        for item in root.findall(".//item"):
            input_dict = {}

            text_elem = item.get("text")
            if text_elem:
                input_dict["name"] = text_elem

            # Use playURL attribute which contains the full Play command with url parameter
            play_url_elem = item.get("playURL")
            if play_url_elem:
                input_dict["playURL"] = play_url_elem

            image_elem = item.get("image")
            if image_elem:
                input_dict["image"] = image_elem

            # Only add inputs that have a playURL (skip non-playable items like "Playlists")
            if input_dict and "playURL" in input_dict:
                inputs.append(input_dict)

        _LOG.debug(f"Inputs: {inputs}")
        return inputs

    async def play_url(self, url: str) -> bool:
        """
        Play a URL (input, stream, etc.).

        Args:
            url: The URL to play

        Returns:
            True on success
        """
        root = await self._request("/Play", {"url": url})
        return root is not None

    async def play_input(self, play_url: str) -> bool:
        """
        Play an input using the playURL from Browse endpoint.

        Args:
            play_url: The playURL string (e.g., "/Play?url=Capture%3Abluez%3Abluetooth")

        Returns:
            True on success
        """
        # The playURL is already properly formatted with URL encoding
        # We need to make the request directly without re-encoding
        if not play_url.startswith("/"):
            _LOG.error(f"Invalid playURL format: {play_url}")
            return False

        await self._ensure_session()

        # Build full URL - playURL already contains endpoint and encoded parameters
        full_url = f"{self.base_url}{play_url}"
        _LOG.info(f"play_input: requesting {full_url}")

        try:
            async with self._session.get(full_url, timeout=aiohttp.ClientTimeout(total=5.0)) as response:
                _LOG.info(f"play_input: actual URL sent: {response.url}")
                if response.status == 200:
                    text = await response.text()
                    _LOG.debug(f"play_input response: {text[:200]}")
                    try:
                        root = ET.fromstring(text)
                        return root is not None
                    except ET.ParseError as e:
                        _LOG.error(f"XML parse error in play_input: {e}")
                        return False
                else:
                    _LOG.error(f"HTTP error {response.status} for play_input")
                    return False
        except Exception as e:
            _LOG.error(f"Error in play_input: {type(e).__name__}: {e}")
            return False
