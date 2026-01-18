"""Spotify Connect Player implementation."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from music_assistant_models.enums import PlaybackState, PlayerFeature, PlayerType
from music_assistant_models.player import DeviceInfo, PlayerMedia, PlayerSource

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
            PlayerFeature.POWER,
            PlayerFeature.PAUSE,
            PlayerFeature.VOLUME_SET,
            PlayerFeature.NEXT_PREVIOUS,
            PlayerFeature.SEEK,
        }
        self._attr_powered = True

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
        # Don't set active_source at startup - let MA send play_media commands
        # active_source will be set to "spotify" when playing external content

    @property
    def maspotconn_provider(self) -> MaspotconnProvider:
        """Return the provider as MaspotconnProvider."""
        return cast("MaspotconnProvider", self.provider)

    @property
    def _source_list(self) -> list[PlayerSource]:
        """Return list of available sources for this player."""
        return [
            PlayerSource(
                id="spotify",
                name="Spotify",
                passive=True,
                can_play_pause=True,
                can_next_previous=True,
                can_seek=True,
            ),
        ]

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
            self.logger.debug("Skipping to next track on device %s", self._device_id)
            await self.maspotconn_provider._spotify_provider._post_data(
                "me/player/next", device_id=self._device_id
            )
            # Poll immediately to update state
            await self.poll()
        except Exception as err:
            self.logger.error("Failed to skip to next track: %s", err)

    async def previous_track(self) -> None:
        """Go to previous track."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot go to previous - no Spotify provider available")
            return

        try:
            self.logger.debug("Going to previous track on device %s", self._device_id)
            await self.maspotconn_provider._spotify_provider._post_data(
                "me/player/previous", device_id=self._device_id
            )
            # Poll immediately to update state
            await self.poll()
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
            self.logger.info(
                "play_media called: uri=%s, title=%s, artist=%s, album=%s, "
                "source_id=%s, queue_item_id=%s",
                media.uri,
                media.title,
                media.artist,
                media.album,
                media.source_id,
                media.queue_item_id,
            )

            spotify_uri = None
            context_uri = None

            # If we have a Spotify URI directly, use it
            if media.uri and media.uri.startswith("spotify:"):
                spotify_uri = media.uri

            # If this is from a Music Assistant queue, try to get the Spotify URI
            if not spotify_uri and media.source_id and media.queue_item_id:
                spotify_uri, context_uri = await self._get_spotify_uri_from_queue(
                    media.source_id, media.queue_item_id
                )

            if not spotify_uri:
                self.logger.warning(
                    "No Spotify URI found for media: %s (uri=%s)", media.title, media.uri
                )
                return

            # Transfer playback to this device first
            await self.maspotconn_provider._spotify_provider._put_data(
                "me/player", data={"device_ids": [self._device_id], "play": True}
            )

            # If we have a context URI (playlist/album), use it to play the full context
            if context_uri:
                self.logger.info(
                    "Playing Spotify context: %s (starting at track %s)",
                    context_uri,
                    spotify_uri,
                )
                await self.maspotconn_provider._spotify_provider._put_data(
                    "me/player/play",
                    data={
                        "device_id": self._device_id,
                        "context_uri": context_uri,
                        "offset": {"uri": spotify_uri},
                    },
                )
            else:
                # Determine if this is a track URI or a context URI
                is_track = spotify_uri.startswith("spotify:track:")
                is_context = any(
                    spotify_uri.startswith(f"spotify:{prefix}:")
                    for prefix in ["playlist", "album", "artist", "show"]
                )

                if not (is_track or is_context):
                    self.logger.warning("Unsupported Spotify URI type: %s", spotify_uri)
                    return

                # Play the media
                if is_track:
                    # For individual tracks, use the uris parameter
                    await self.maspotconn_provider._spotify_provider._put_data(
                        "me/player/play",
                        data={"device_id": self._device_id, "uris": [spotify_uri]},
                    )
                else:
                    # For context URIs (playlists, albums, etc.)
                    await self.maspotconn_provider._spotify_provider._put_data(
                        "me/player/play",
                        data={"device_id": self._device_id, "context_uri": spotify_uri},
                    )

            # Update state
            self._attr_current_media = media
            self._attr_playback_state = PlaybackState.PLAYING
            self.update_state(force_update=True)

            # Poll immediately to sync state
            await self.poll()

        except Exception as err:
            self.logger.error("Failed to play media: %s", err)

    async def _get_spotify_uri_from_queue(
        self, queue_id: str, queue_item_id: str
    ) -> tuple[str | None, str | None]:
        """Extract Spotify URI and context URI from a Music Assistant queue item.

        Returns:
            tuple: (track_uri, context_uri) - context_uri is set if this is the first
                   track of a playlist/album that should be played as a context.
        """
        try:
            # Access the queue items from the player_queues controller
            queue_items = self.mass.player_queues._queue_items.get(queue_id, [])
            queue = self.mass.player_queues._queues.get(queue_id)

            track_uri = None
            context_uri = None

            # Find the queue item
            item_index = None
            for idx, queue_item in enumerate(queue_items):
                if queue_item.queue_item_id == queue_item_id:
                    item_index = idx
                    # Get the track URI from provider mappings
                    if queue_item.media_item:
                        for mapping in getattr(queue_item.media_item, "provider_mappings", []):
                            if mapping.provider_domain == "spotify":
                                track_uri = f"spotify:track:{mapping.item_id}"
                                self.logger.debug("Found Spotify track URI: %s", track_uri)
                                break
                    break

            # Check if this is the first item and we have a playlist/album context
            if item_index == 0 and queue and hasattr(queue, "enqueued_media_items"):
                enqueued = queue.enqueued_media_items
                if enqueued:
                    # Get the original media item that was enqueued
                    original_item = enqueued[0] if enqueued else None
                    if original_item:
                        # Check if it's a playlist or album
                        media_type = getattr(original_item, "media_type", None)
                        if media_type and str(media_type) in ("playlist", "album"):
                            # Get the Spotify URI for the context
                            for mapping in getattr(original_item, "provider_mappings", []):
                                if mapping.provider_domain == "spotify":
                                    context_uri = f"spotify:{media_type}:{mapping.item_id}"
                                    self.logger.debug("Found Spotify context URI: %s", context_uri)
                                    break

            if not track_uri:
                self.logger.debug("Could not find Spotify URI for queue_item_id=%s", queue_item_id)

            return track_uri, context_uri

        except Exception as err:
            self.logger.debug("Error getting Spotify URI from queue: %s", err)
        return None, None

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

            # Don't set active_source - this allows MA to send play_media commands
            # The source_list with "spotify" source handles skip/seek controls

            # Update volume
            volume = device.get("volume_percent")
            if volume is not None:
                self._attr_volume_level = volume

            # Update current media
            item = playback_data.get("item", {})
            if item:
                artists = ", ".join(a["name"] for a in item.get("artists", []))
                album_info = item.get("album", {})
                album = album_info.get("name", "")

                # Get album image URL (prefer largest image)
                image_url = None
                images = album_info.get("images", [])
                if images:
                    # Spotify returns images sorted by size, largest first
                    image_url = images[0].get("url")

                # Update current media directly (bypass queue/group logic)
                media = PlayerMedia(
                    uri=item.get("uri", ""),
                    title=item.get("name", "Unknown"),
                    artist=artists,
                    album=album,
                    duration=item.get("duration_ms", 0) // 1000,
                    image_url=image_url,
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
