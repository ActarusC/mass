"""Spotify Connect Player implementation."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from music_assistant_models.enums import PlaybackState, PlayerFeature, PlayerType
from music_assistant_models.player import DeviceInfo, PlayerMedia

from music_assistant.models.player import Player

if TYPE_CHECKING:
    from .provider import MaspotconnProvider


class SpotifyConnectPlayer(Player):
    """Represents a Spotify Connect device controlled via Spotify Web API."""

    _provider: MaspotconnProvider

    def __init__(
        self,
        provider: MaspotconnProvider,
        player_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the Player."""
        super().__init__(provider, player_id)
        self._provider = provider

        # Store Spotify device ID
        self._device_id: str = device_info["id"]

        # Set player attributes
        self._attr_name = device_info["name"]
        self._attr_type = PlayerType.PLAYER
        self._attr_supported_features = {
            PlayerFeature.PAUSE,
            PlayerFeature.VOLUME_SET,
            PlayerFeature.NEXT_PREVIOUS,
            PlayerFeature.SEEK,
        }

        # Set device info
        device_type: str = device_info.get("type", "Speaker")
        self._attr_device_info = DeviceInfo(
            model=device_type,
            manufacturer="Spotify Connect",
        )

        # Initialize state
        volume: int = device_info.get("volume_percent", 50)
        self._attr_volume_level = volume
        self._attr_playback_state = PlaybackState.IDLE

    @property
    def maspotconn_provider(self) -> MaspotconnProvider:
        """Return the provider as MaspotconnProvider."""
        return cast("MaspotconnProvider", self.provider)

    @property
    def needs_poll(self) -> bool:
        """Return if the player needs to be polled for state updates."""
        return True

    @property
    def poll_interval(self) -> int:
        """Return the interval in seconds to poll the player for state updates."""
        # Poll more frequently when playing
        return 5 if self.playback_state == PlaybackState.PLAYING else 30

    async def play(self) -> None:
        """Resume playback on this device."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot play - no Spotify provider available")
            return

        try:
            self.logger.debug("Sending play command to device %s", self._device_id)
            await self.maspotconn_provider._spotify_provider._put_data(
                "me/player/play", data={"device_id": self._device_id}
            )
            # Optimistically update state
            self._attr_playback_state = PlaybackState.PLAYING
            self.update_state(force_update=True)
        except Exception as err:
            self.logger.error("Failed to send play command: %s", err)

    async def pause(self) -> None:
        """Pause playback."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot pause - no Spotify provider available")
            return

        try:
            self.logger.debug("Sending pause command")
            await self.maspotconn_provider._spotify_provider._put_data("me/player/pause")
            # Optimistically update state
            self._attr_playback_state = PlaybackState.PAUSED
            self.update_state(force_update=True)
        except Exception as err:
            self.logger.error("Failed to send pause command: %s", err)

    async def stop(self) -> None:
        """Stop playback."""
        # Spotify doesn't have a stop command, we use pause
        await self.pause()
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_current_media = None
        self.update_state(force_update=True)

    async def volume_set(self, volume_level: int) -> None:
        """Set volume level (0-100)."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot set volume - no Spotify provider available")
            return

        try:
            self.logger.debug("Setting volume to %d%%", volume_level)
            await self.maspotconn_provider._spotify_provider._put_data(
                f"me/player/volume?volume_percent={volume_level}"
            )
            # Optimistically update state
            self._attr_volume_level = volume_level
            self.update_state(force_update=True)
        except Exception as err:
            self.logger.error("Failed to set volume: %s", err)

    async def next_track(self) -> None:
        """Skip to next track."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot skip - no Spotify provider available")
            return

        try:
            self.logger.debug("Skipping to next track")
            await self.maspotconn_provider._spotify_provider._post_data("me/player/next")
        except Exception as err:
            self.logger.error("Failed to skip to next track: %s", err)

    async def previous_track(self) -> None:
        """Go to previous track."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot go to previous - no Spotify provider available")
            return

        try:
            self.logger.debug("Going to previous track")
            await self.maspotconn_provider._spotify_provider._post_data("me/player/previous")
        except Exception as err:
            self.logger.error("Failed to go to previous track: %s", err)

    async def seek(self, position: int) -> None:
        """Seek to position in seconds."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot seek - no Spotify provider available")
            return

        try:
            position_ms = position * 1000
            self.logger.debug("Seeking to position %d seconds (%d ms)", position, position_ms)
            await self.maspotconn_provider._spotify_provider._put_data(
                f"me/player/seek?position_ms={position_ms}"
            )
        except Exception as err:
            self.logger.error("Failed to seek: %s", err)

    async def play_media(self, media: PlayerMedia) -> None:
        """Play media on this device."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot play media - no Spotify provider available")
            return

        try:
            self.logger.info("Playing media: %s", media.uri)

            # Transfer playback to this device
            await self.maspotconn_provider._spotify_provider._put_data(
                "me/player", data={"device_ids": [self._device_id], "play": True}
            )

            # If we have a Spotify URI, play it
            if media.uri and media.uri.startswith("spotify:"):
                await self.maspotconn_provider._spotify_provider._put_data(
                    "me/player/play", data={"device_id": self._device_id, "uris": [media.uri]}
                )

            # Update state
            self._attr_current_media = media
            self._attr_playback_state = PlaybackState.PLAYING
            self.update_state(force_update=True)

        except Exception as err:
            self.logger.error("Failed to play media: %s", err)

    async def poll(self) -> None:
        """Poll player for state updates."""
        if not self.maspotconn_provider._spotify_provider:
            return

        try:
            # Get current playback state directly from Spotify API (no cache)
            auth_info = await self.maspotconn_provider._spotify_provider._get_auth_info()
            if not auth_info:
                return

            # Call API directly to bypass cache
            playback_data = await self.maspotconn_provider._spotify_provider._get_data("me/player")

            if not playback_data:
                # No active playback
                self._attr_playback_state = PlaybackState.IDLE
                self._attr_current_media = None
                self._attr_active_source = None
                self.update_state(force_update=True)
                return

            # Check if this device is the active one
            device = playback_data.get("device", {})
            if device.get("id") != self._device_id:
                # Not playing on this device
                self._attr_playback_state = PlaybackState.IDLE
                self._attr_current_media = None
                self._attr_active_source = None
                self.update_state(force_update=True)
                return

            # Update playback state
            is_playing = playback_data.get("is_playing", False)
            self._attr_playback_state = (
                PlaybackState.PLAYING if is_playing else PlaybackState.PAUSED
            )

            # Set active source to "spotify" to prevent queue lookup
            # This ensures __calculate_current_media() uses self._current_media instead of queue
            self._attr_active_source = "spotify"

            # Update volume
            volume = device.get("volume_percent")
            if volume is not None:
                self._attr_volume_level = volume

            # Update current media
            item = playback_data.get("item", {})
            if item:
                artists = ", ".join(a["name"] for a in item.get("artists", []))
                album = item.get("album", {}).get("name", "")

                # Update current media directly (bypass queue/group logic)
                media = PlayerMedia(
                    uri=item.get("uri", ""),
                    title=item.get("name", "Unknown"),
                    artist=artists,
                    album=album,
                    duration=item.get("duration_ms", 0) // 1000,
                )
                self._Player__attr_current_media = media
                self._attr_current_media = media

                self.logger.debug("Updated current media: %s by %s", item.get("name"), artists)
                self.logger.debug(
                    "Current media property returns: %s",
                    self.current_media.title if self.current_media else "None",
                )
            else:
                # No item, clear current media
                self._Player__attr_current_media = None  # type: ignore[assignment]

            # Update elapsed time
            progress_ms = playback_data.get("progress_ms", 0)
            self._attr_elapsed_time = progress_ms / 1000
            self._attr_elapsed_time_last_updated = time.time()

            # Notify Music Assistant of state changes
            self.logger.debug("Calling update_state(force_update=True)")
            self.update_state(force_update=True)
            self.logger.debug("update_state() completed")

        except Exception as err:
            self.logger.debug("Failed to poll player state: %s", err)

    async def async_play(self) -> None:
        """Resume playback."""
        if not self.maspotconn_provider._spotify_provider:
            return

        try:
            auth_info = await self.maspotconn_provider._spotify_provider._get_auth_info()
            if not auth_info:
                return

            # Call Spotify API to resume playback
            await self.maspotconn_provider._spotify_provider._get_data(
                f"me/player/play?device_id={self._device_id}", method="PUT"
            )
            self.logger.debug("Resumed playback")

            # Poll immediately to update state
            await self.poll()
        except Exception as err:
            self.logger.error("Failed to resume playback: %s", err)

    async def async_pause(self) -> None:
        """Pause playback."""
        if not self.maspotconn_provider._spotify_provider:
            return

        try:
            auth_info = await self.maspotconn_provider._spotify_provider._get_auth_info()
            if not auth_info:
                return

            # Call Spotify API to pause playback
            await self.maspotconn_provider._spotify_provider._get_data(
                f"me/player/pause?device_id={self._device_id}", method="PUT"
            )
            self.logger.debug("Paused playback")

            # Poll immediately to update state
            await self.poll()
        except Exception as err:
            self.logger.error("Failed to pause playback: %s", err)

    async def async_next_track(self) -> None:
        """Skip to next track."""
        if not self.maspotconn_provider._spotify_provider:
            return

        try:
            # Call Spotify API to skip to next track
            await self.maspotconn_provider._spotify_provider._post_data(
                "me/player/next", device_id=self._device_id, want_result=False
            )
            self.logger.debug("Skipped to next track")

            # Poll immediately to update state
            await self.poll()
        except Exception as err:
            self.logger.error("Failed to skip to next track: %s", err)

    async def async_previous_track(self) -> None:
        """Skip to previous track."""
        if not self.maspotconn_provider._spotify_provider:
            return

        try:
            # Call Spotify API to skip to previous track
            await self.maspotconn_provider._spotify_provider._post_data(
                "me/player/previous", device_id=self._device_id, want_result=False
            )
            self.logger.debug("Skipped to previous track")

            # Poll immediately to update state
            await self.poll()
        except Exception as err:
            self.logger.error("Failed to skip to previous track: %s", err)

    async def async_set_volume_level(self, volume_level: int) -> None:
        """Set volume level (0-100)."""
        if not self.maspotconn_provider._spotify_provider:
            return

        try:
            auth_info = await self.maspotconn_provider._spotify_provider._get_auth_info()
            if not auth_info:
                return

            # Clamp volume to 0-100
            volume = max(0, min(100, int(volume_level * 100)))

            # Call Spotify API to set volume
            await self.maspotconn_provider._spotify_provider._get_data(
                f"me/player/volume?volume_percent={volume}&device_id={self._device_id}",
                method="PUT",
            )
            self.logger.debug("Set volume to %d%%", volume)

            # Poll immediately to update state
            await self.poll()
        except Exception as err:
            self.logger.error("Failed to set volume: %s", err)
