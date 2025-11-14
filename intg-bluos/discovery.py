"""BluOS device discovery via mDNS/Zeroconf."""
import asyncio
import logging
from typing import Callable, Optional
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncZeroconf

_LOG = logging.getLogger(__name__)

# BluOS service type
BLUOS_SERVICE_TYPE = "_musc._tcp.local."


class BluOSListener(ServiceListener):
    """Listener for BluOS mDNS services."""

    def __init__(self, callback: Callable[[dict], None], loop: asyncio.AbstractEventLoop):
        """
        Initialize listener.

        Args:
            callback: Async function to call when device is discovered
            loop: Event loop to schedule callbacks on
        """
        self._callback = callback
        self._loop = loop
        self._discovered = set()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle service addition."""
        _LOG.info(f"BluOS service discovered: {name}")

        info = zc.get_service_info(type_, name)
        if info:
            # Extract IP address
            if info.addresses:
                host = socket.inet_ntoa(info.addresses[0])
            else:
                _LOG.warning(f"No address for service {name}")
                return

            # Extract port (default 11000)
            port = info.port or 11000

            # Extract device info from properties
            props = {}
            if info.properties:
                for key, value in info.properties.items():
                    try:
                        props[key.decode('utf-8')] = value.decode('utf-8')
                    except:
                        pass

            # Get device name from mDNS name or properties
            # Remove the service type suffix from the name
            device_name = name.replace(f".{BLUOS_SERVICE_TYPE}", "").replace(".", " ").strip()
            if not device_name:
                device_name = "BluOS Device"

            # Only prepend model name if it's not already in the device name
            if "model" in props:
                model = props.get("model", "BluOS")
                if model not in device_name:
                    device_name = f"{model} - {device_name}"

            device_key = f"{host}:{port}"
            if device_key in self._discovered:
                return

            self._discovered.add(device_key)

            device_info = {
                "name": device_name,
                "host": host,
                "port": port,
                "service_name": name,
                "properties": props
            }

            _LOG.info(f"Discovered BluOS device: {device_name} at {host}:{port}")
            _LOG.debug(f"Device properties: {props}")

            # Call callback from zeroconf thread using run_coroutine_threadsafe
            if self._callback:
                asyncio.run_coroutine_threadsafe(
                    self._callback(device_info),
                    self._loop
                )

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle service removal."""
        _LOG.info(f"BluOS service removed: {name}")

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle service update."""
        _LOG.debug(f"BluOS service updated: {name}")


class BluOSDeviceDiscovery:
    """mDNS-based BluOS device discovery manager."""

    def __init__(self):
        """Initialize discovery manager."""
        self._running = False
        self._azc: Optional[AsyncZeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._listener: Optional[BluOSListener] = None

    async def start(self, callback: Callable[[dict], None]) -> None:
        """
        Start BluOS device discovery via mDNS.

        Args:
            callback: Async function to call when devices are discovered
        """
        if self._running:
            _LOG.warning("Discovery already running")
            return

        _LOG.info(f"Starting BluOS mDNS discovery for service type: {BLUOS_SERVICE_TYPE}")
        self._running = True

        try:
            # Get the current event loop
            loop = asyncio.get_event_loop()

            # Create AsyncZeroconf instance
            self._azc = AsyncZeroconf()

            # Create listener with event loop
            self._listener = BluOSListener(callback, loop)

            # Create browser
            self._browser = ServiceBrowser(
                self._azc.zeroconf,
                BLUOS_SERVICE_TYPE,
                self._listener
            )

            _LOG.info("BluOS mDNS discovery started successfully")

        except Exception as e:
            _LOG.error(f"Failed to start mDNS discovery: {e}", exc_info=True)
            await self.stop()

    async def stop(self) -> None:
        """Stop discovery."""
        if not self._running:
            return

        _LOG.info("Stopping BluOS mDNS discovery")
        self._running = False

        if self._browser:
            try:
                self._browser.cancel()
            except Exception as e:
                _LOG.debug(f"Error canceling browser: {e}")
            self._browser = None

        if self._azc:
            try:
                await self._azc.async_close()
            except Exception as e:
                _LOG.debug(f"Error closing AsyncZeroconf: {e}")
            self._azc = None

        self._listener = None


# Import socket for inet_ntoa
import socket
