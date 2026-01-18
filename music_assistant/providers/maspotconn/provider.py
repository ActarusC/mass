"""Spotify Connect Player Provider implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from music_assistant_models.enums import EventType

from music_assistant.models.player_provider import PlayerProvider

from .player import SpotifyConnectPlayer

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.event import MassEvent
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.providers.spotify.provider import SpotifyProvider


class MaspotconnProvider(PlayerProvider):
    """Player provider for Spotify Connect devices via Web API."""

    def __init__(
        self,
        mass: MusicAssistant,
        manifest: ProviderManifest,
        config: ProviderConfig,
    ) -> None:
        """Initialize the provider."""
        super().__init__(mass, manifest, config)
        self._spotify_provider: SpotifyProvider | None = None

    async def handle_async_init(self) -> None:
        """Handle async initialization of the provider."""
        self.logger.info("Initializing Spotify Connect Player Provider")

        # Find the Spotify music provider
        await self._find_spotify_provider()

        # Subscribe to provider events to detect when Spotify provider is added/removed
        self.mass.subscribe(
            self._on_provider_event,
            EventType.PROVIDERS_UPDATED,
        )

        if not self._spotify_provider:
            self.logger.warning(
                "No Spotify music provider found. "
                "Please configure the Spotify music provider first. "
                "Maspotconn requires it for authentication and API access."
            )
        else:
            # Trigger discovery immediately if Spotify provider is available
            self.logger.info("Spotify provider found, triggering device discovery")
            await self.discover_players()

    async def loaded_in_mass(self) -> None:
        """Call after the provider has been loaded."""
        self.logger.info("Maspotconn provider loaded")
        if self._spotify_provider:
            await self.discover_players()
        else:
            self.logger.warning(
                "Cannot discover Spotify Connect devices without Spotify music provider"
            )

    async def unload(self, is_removed: bool = False) -> None:
        """Handle unload/close of the provider."""
        self.logger.info("Unloading Maspotconn provider")
        for player in self.players:
            await self.mass.players.unregister(player.player_id)

    async def discover_players(self) -> None:
        """Discover Spotify Connect devices via Web API."""
        if not self._spotify_provider:
            self.logger.warning("Cannot discover devices - no Spotify provider available")
            return

        try:
            self.logger.info("🔍 DISCOVERING Spotify Connect devices...")

            # Get list of available devices from Spotify Web API
            devices_data = await self._spotify_provider._get_data("me/player/devices")
            devices = devices_data.get("devices", [])

            self.logger.info("✅ Found %d Spotify device(s)", len(devices))

            for device in devices:
                device_id = device.get("id")
                device_name = device.get("name")
                device_type = device.get("type")

                if not device_id:
                    continue

                self.logger.debug(
                    "Found device: %s (type: %s, id: %s)",
                    device_name,
                    device_type,
                    device_id,
                )

                # Check if we already have this player registered
                player_id = f"maspotconn_{device_id}"
                if mass_player := self.mass.players.get(player_id):
                    self.logger.debug("Player %s already registered, updating...", device_name)
                    # Update existing player
                    mass_player.update_state()
                else:
                    # Register new player
                    self.logger.info("Registering new Spotify Connect player: %s", device_name)
                    player = SpotifyConnectPlayer(
                        provider=self,
                        player_id=player_id,
                        device_info=device,
                    )
                    await self.mass.players.register(player)

        except Exception as err:
            self.logger.exception("Failed to discover Spotify devices: %s", err)

    async def _find_spotify_provider(self) -> None:
        """Find and link to the Spotify music provider."""
        for provider in self.mass.music.providers:
            if provider.domain == "spotify":
                self._spotify_provider = cast("SpotifyProvider", provider)
                self.logger.info(
                    "Found Spotify music provider '%s' - will use its authentication",
                    provider.name,
                )
                # Trigger device discovery now that Spotify is available
                self.logger.info(
                    "Triggering device discovery now that Spotify provider is available"
                )
                await self.discover_players()
                return

        self.logger.warning(
            "No Spotify music provider found. "
            "Maspotconn requires the Spotify music provider for authentication."
        )

    def _on_provider_event(self, event: MassEvent) -> None:
        """Handle provider added/removed events."""
        # Re-check for Spotify provider when providers change
        self.mass.create_task(self._find_spotify_provider())
        if self._spotify_provider:
            self.mass.create_task(self.discover_players())
