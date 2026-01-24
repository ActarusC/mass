"""Spotify Connect Player implementation."""

from __future__ import annotations

import asyncio
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
        self._provider = provider
        self._device_id: str = device_info["id"]

        # Enhanced attributes for reconnection (initialize BEFORE super().__init__)
        self._attr_available = device_info.get("is_active", False)
        self._attr_is_restricted = device_info.get("is_restricted", False)
        self._last_seen = time.time()
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3
        self._reconnect_cooldown = 30  # seconds between reconnect attempts

        # Set player attributes
        self._attr_name = device_info["name"]
        self._attr_type = PlayerType.PLAYER
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

        # Call parent constructor
        super().__init__(provider, player_id)

        # Set supported features AFTER super().__init__ (it resets them to empty set)
        self._attr_supported_features = {
            PlayerFeature.POWER,
            PlayerFeature.PAUSE,
            PlayerFeature.VOLUME_SET,
            PlayerFeature.NEXT_PREVIOUS,
            PlayerFeature.SEEK,
        }

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
        # Poll more frequently when playing or when trying to reconnect
        if self.playback_state == PlaybackState.PLAYING:
            return 5
        elif not self.available and self._reconnect_attempts < self._max_reconnect_attempts:
            return 10  # Poll more frequently when trying to reconnect
        else:
            return 30

    @property
    def available(self) -> bool:
        """Return if the player is available.
        
        For Zeroconf-discovered devices, we mark them as available even if
        not currently active in Spotify. The actual activation happens when
        the user tries to play music.
        """
        if self._attr_is_restricted:
            return False
        
        # Always available if discovered via Zeroconf (has zeroconf info)
        device_info = self.maspotconn_provider._device_cache.get(self._device_id, {})
        if device_info.get("zeroconf_ip"):
            return True
        
        # Consider device available if seen recently (within last 5 minutes)
        if time.time() - self._last_seen < 300:
            return True
        
        return self._attr_available

    async def force_reconnect(self) -> bool:
        """Forcer la reconnexion du device."""
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            self.logger.warning("Max reconnect attempts reached for %s", self._device_id)
            return False
        
        self._reconnect_attempts += 1
        self.logger.info("Attempting to reconnect device %s (attempt %d/%d)", 
                        self._device_id, self._reconnect_attempts, self._max_reconnect_attempts)
        
        try:
            # Essayer de transférer la lecture vers ce device
            # Cela peut "réveiller" le device et le rendre disponible
            transfer_data = {
                "device_ids": [self._device_id],
                "play": False
            }
            
            await self.maspotconn_provider._spotify_provider._put_data(
                "me/player", data=transfer_data
            )
            
            # Attendre un peu et vérifier si le device est maintenant actif
            await asyncio.sleep(2)
            
            # Vérifier les devices disponibles
            devices_data = await self.maspotconn_provider._spotify_provider._get_data("me/player/devices")
            devices = devices_data.get("devices", [])
            
            for device in devices:
                if device.get("id") == self._device_id:
                    if device.get("is_active", False):
                        self.logger.info("Successfully reconnected device %s", self._device_id)
                        self._attr_available = True
                        self._last_seen = time.time()
                        self._reconnect_attempts = 0
                        return True
                    break
            
            self.logger.warning("Reconnect attempt %d failed for %s", self._reconnect_attempts, self._device_id)
            return False
            
        except Exception as e:
            self.logger.error("Error during reconnect attempt for %s: %s", self._device_id, e)
            return False

    async def play(self) -> None:
        """Resume playback on this device."""
        if not self.maspotconn_provider._spotify_provider:
            self.logger.warning("Cannot play - no Spotify provider available")
            return

        try:
            # Enhanced reconnection logic
            if not self.available:
                if await self.force_reconnect():
                    self.logger.info("Reconnected successfully, proceeding with play")
                else:
                    self.logger.warning("Cannot play - device unavailable")
                    return

            self.logger.debug("Sending play command to device %s", self._device_id)
            await self.maspotconn_provider._spotify_provider._put_data(
                "me/player/play", data={"device_id": self._device_id}
            )
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
                                    item_id = mapping.item_id
                                    # Validate that this is a real Spotify ID
                                    # Spotify IDs are 22 alphanumeric characters
                                    # Skip internal MA IDs like "liked_songs-spotify--xxx"
                                    if item_id and len(item_id) == 22 and item_id.isalnum():
                                        context_uri = f"spotify:{media_type}:{item_id}"
                                        self.logger.debug(
                                            "Found Spotify context URI: %s", context_uri
                                        )
                                    else:
                                        self.logger.debug(
                                            "Skipping invalid Spotify ID: %s", item_id
                                        )
                                    break

            if not track_uri:
                self.logger.debug("Could not find Spotify URI for queue_item_id=%s", queue_item_id)

            return track_uri, context_uri

        except Exception as err:
            self.logger.debug("Error getting Spotify URI from queue: %s", err)
        return None, None

    async def poll(self) -> None:
        """Enhanced poll method - only updates state, no automatic reconnection."""
        if not self.maspotconn_provider._spotify_provider:
            return

        try:
            # Get current playback state directly from Spotify API
            auth_info = await self.maspotconn_provider._spotify_provider._get_auth_info()
            if not auth_info:
                return

            # Call API directly to bypass cache
            playback_data = await self.maspotconn_provider._spotify_provider._get_data("me/player")

            if not playback_data:
                # No active playback - check if our device exists in device list
                devices_data = await self.maspotconn_provider._spotify_provider._get_data("me/player/devices")
                devices = devices_data.get("devices", [])
                
                for device in devices:
                    if device.get("id") == self._device_id:
                        self._last_seen = time.time()
                        self._attr_is_restricted = device.get("is_restricted", False)
                        # Device exists, mark as idle but don't try to reconnect
                        break
                
                self._attr_playback_state = PlaybackState.IDLE
                self._attr_current_media = None
                self._attr_active_source = None
                self.update_state(force_update=True)
                return

            # Check if this device is the active one
            device = playback_data.get("device", {})
            if device.get("id") != self._device_id:
                # Not playing on this device - just update state, don't reconnect
                self._attr_playback_state = PlaybackState.IDLE
                self._attr_current_media = None
                self._attr_active_source = None
                self.update_state(force_update=True)
                return
            
            # This device is active - update last seen and state
            self._last_seen = time.time()
            if self._reconnect_attempts > 0:
                self.logger.info("Device %s is stable, reset reconnect attempts", self._device_id)
                self._reconnect_attempts = 0

            # Update playback state
            is_playing = playback_data.get("is_playing", False)
            self._attr_playback_state = (
                PlaybackState.PLAYING if is_playing else PlaybackState.PAUSED
            )

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

                # Get album image URL
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
                self._Player__attr_current_media = None

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
