"""BluOS media player entity."""
import asyncio
import logging
from typing import Any

from ucapi import media_player, StatusCodes
from ucapi_framework import MediaPlayerEntity

from config import BluOSDeviceConfig
from device import BluOSDevice

_LOG = logging.getLogger(__name__)

FEATURES = [
    media_player.Features.PLAY_PAUSE,
    media_player.Features.STOP,
    media_player.Features.NEXT,
    media_player.Features.PREVIOUS,
    media_player.Features.VOLUME,
    media_player.Features.VOLUME_UP_DOWN,
    media_player.Features.MUTE_TOGGLE,
    media_player.Features.MUTE,
    media_player.Features.UNMUTE,
    media_player.Features.SHUFFLE,
    media_player.Features.REPEAT,
    media_player.Features.SELECT_SOURCE,
    media_player.Features.MEDIA_TITLE,
    media_player.Features.MEDIA_ARTIST,
    media_player.Features.MEDIA_ALBUM,
]

_STATE_MAP = {
    "PLAYING": media_player.States.PLAYING,
    "PAUSED": media_player.States.PAUSED,
    "ON": media_player.States.ON,
    "UNAVAILABLE": media_player.States.UNAVAILABLE,
}

_REPEAT_MAP = {
    "ALL": media_player.RepeatMode.ALL,
    "ONE": media_player.RepeatMode.ONE,
    "OFF": media_player.RepeatMode.OFF,
}


class BluOSMediaPlayer(MediaPlayerEntity):
    """Media player entity for BluOS devices."""

    def __init__(self, device_config: BluOSDeviceConfig, device: BluOSDevice) -> None:
        self._device = device
        # Keep the pre-0.2.5 entity ID scheme ("bluos_<host>", no dot) so
        # upgrading never breaks existing activity references - see
        # BluOSDriver.device_from_entity_id() for the matching parse side.
        entity_id = f"bluos_{device_config.identifier}"
        super().__init__(
            entity_id,
            device_config.name,
            features=FEATURES,
            attributes={
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
                media_player.Attributes.VOLUME: 50,
                media_player.Attributes.MUTED: False,
                media_player.Attributes.SHUFFLE: False,
                media_player.Attributes.REPEAT: media_player.RepeatMode.OFF,
            },
            device_class=media_player.DeviceClasses.SPEAKER,
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        d = self._device

        if d.state == "UNAVAILABLE":
            self.set_unavailable()
            return

        attrs: dict[str, Any] = {
            media_player.Attributes.STATE: _STATE_MAP.get(d.state, media_player.States.UNKNOWN),
            media_player.Attributes.VOLUME: d.volume,
            media_player.Attributes.MUTED: d.muted,
            media_player.Attributes.SHUFFLE: d.shuffle,
            media_player.Attributes.REPEAT: _REPEAT_MAP.get(d.repeat, media_player.RepeatMode.OFF),
            # Always set title/artist/album (even None) to clear stale info from the UI
            # when switching to a source that has none (e.g. Capture inputs).
            media_player.Attributes.MEDIA_TITLE: d.title,
            media_player.Attributes.MEDIA_ARTIST: d.artist,
            media_player.Attributes.MEDIA_ALBUM: d.album,
        }

        if d.image_url:
            attrs[media_player.Attributes.MEDIA_IMAGE_URL] = d.image_url
        if d.source:
            attrs[media_player.Attributes.SOURCE] = d.source
        if d.source_list:
            attrs[media_player.Attributes.SOURCE_LIST] = d.source_list

        self.update(attrs)

    async def _handle_command(
        self, entity: media_player.MediaPlayer, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        try:
            return await self._dispatch_command(cmd_id, params)
        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("[%s] Command error: %s", self.id, err)
            return StatusCodes.SERVER_ERROR

    async def _dispatch_command(self, cmd_id: str, params: dict[str, Any] | None) -> StatusCodes:
        d = self._device
        needs_refresh = True

        if cmd_id == media_player.Commands.PLAY_PAUSE:
            ok = await d.play_pause()
        elif cmd_id == media_player.Commands.STOP:
            ok = await d.stop()
        elif cmd_id == media_player.Commands.NEXT:
            ok = await d.next_track()
        elif cmd_id == media_player.Commands.PREVIOUS:
            ok = await d.previous_track()
        elif cmd_id == media_player.Commands.VOLUME:
            if not params or "volume" not in params:
                return StatusCodes.BAD_REQUEST
            ok = await d.set_volume(int(params["volume"]))
            needs_refresh = False
        elif cmd_id == media_player.Commands.VOLUME_UP:
            ok = await d.volume_up()
            needs_refresh = False
        elif cmd_id == media_player.Commands.VOLUME_DOWN:
            ok = await d.volume_down()
            needs_refresh = False
        elif cmd_id == media_player.Commands.MUTE_TOGGLE:
            ok = await d.mute_toggle()
            needs_refresh = False
        elif cmd_id == media_player.Commands.MUTE:
            ok = await d.set_mute(True)
            needs_refresh = False
        elif cmd_id == media_player.Commands.UNMUTE:
            ok = await d.set_mute(False)
            needs_refresh = False
        elif cmd_id == media_player.Commands.SHUFFLE:
            if not params or "shuffle" not in params:
                return StatusCodes.BAD_REQUEST
            ok = await d.set_shuffle(params["shuffle"])
        elif cmd_id == media_player.Commands.REPEAT:
            if not params or "repeat" not in params:
                return StatusCodes.BAD_REQUEST
            ok = await d.set_repeat(params["repeat"])
        elif cmd_id == media_player.Commands.SELECT_SOURCE:
            if not params or "source" not in params:
                return StatusCodes.BAD_REQUEST
            ok = await d.select_source(params["source"])
        else:
            return StatusCodes.NOT_IMPLEMENTED

        if needs_refresh:
            # BluOS needs a moment to reflect the change before /Status returns it.
            await asyncio.sleep(0.1)
            await d.refresh_status()

        return StatusCodes.OK if ok else StatusCodes.SERVER_ERROR
