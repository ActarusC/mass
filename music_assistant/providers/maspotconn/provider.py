"""Spotify Connect Player Provider implementation."""

from __future__ import annotations

import asyncio
import json
import os
import aiohttp
from typing import TYPE_CHECKING, Any, cast

from music_assistant_models.enums import EventType

from music_assistant.models.player_provider import PlayerProvider

from .player import SpotifyConnectPlayer
from .zeroconf_auth import authenticate_with_device

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.event import MassEvent
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.providers.spotify.provider import SpotifyProvider

# How often to poll for new devices (in seconds)
DEVICE_DISCOVERY_INTERVAL = 60

# Known Spotify Connect devices (discovered via Zeroconf)
# Format: device_id -> {ip, port, path, name}
KNOWN_ZEROCONF_DEVICES = {
    "5fa0918ceda03be082806b71a0d509caddd544f9": {
        "ip": "10.0.0.119",
        "port": 5389,
        "path": "/zc",
        "name": "JBL Authentics 500"
    }
}


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
        # Cache of known devices (device_id -> device_info)
        self._device_cache: dict[str, dict[str, Any]] = {}
        self._discovery_task: asyncio.Task[None] | None = None

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

        # Start periodic device discovery
        self._discovery_task = asyncio.create_task(self._periodic_discovery())

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
        # Stop periodic discovery
        if self._discovery_task:
            self._discovery_task.cancel()
            self._discovery_task = None
        for player in self.players:
            await self.mass.players.unregister(player.player_id)

    async def discover_players(self) -> None:
        """Enhanced device discovery with forced activation."""
        if not self._spotify_provider:
            self.logger.warning("Cannot discover devices - no Spotify provider available")
            return

        try:
            self.logger.info("🔍 DISCOVERING Spotify Connect devices...")

            # Get list of available devices from Spotify Web API
            devices_data = await self._spotify_provider._get_data("me/player/devices")
            devices = devices_data.get("devices", [])

            self.logger.info("✅ Found %d active Spotify device(s)", len(devices))

            # Update cache with newly discovered devices
            for device in devices:
                device_id = device.get("id")
                if device_id:
                    self._device_cache[device_id] = device

            # Try to force activation of known JBL device
            await self._force_jbl_activation()

            # Register players for all cached devices (including inactive ones)
            for device_id, device in self._device_cache.items():
                device_name = device.get("name")
                device_type = device.get("type")

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

    async def _force_jbl_activation(self) -> None:
        """Forcer l'activation du JBL via Zeroconf avec authentification DH."""
        if not self._spotify_provider:
            self.logger.warning("Cannot activate JBL - no Spotify provider available")
            return
        
        # Load librespot credentials
        credentials = await self._load_librespot_credentials()
        if not credentials:
            self.logger.warning("No librespot credentials found")
            return
        
        for device_id, zeroconf_info in KNOWN_ZEROCONF_DEVICES.items():
            # Always ensure zeroconf info is in cache, even if device already discovered via API
            if device_id in self._device_cache:
                # Device exists, just add zeroconf info if missing
                if "zeroconf_ip" not in self._device_cache[device_id]:
                    self._device_cache[device_id]["zeroconf_ip"] = zeroconf_info["ip"]
                    self._device_cache[device_id]["zeroconf_port"] = zeroconf_info["port"]
                    self._device_cache[device_id]["zeroconf_path"] = zeroconf_info["path"]
                    self.logger.debug("Added Zeroconf info to existing device %s", zeroconf_info["name"])
                continue
            
            self.logger.info("🔄 Attempting to activate %s via Zeroconf DH auth...", zeroconf_info["name"])
            
            try:
                # Step 1: Get device info from Zeroconf API
                async with aiohttp.ClientSession() as session:
                    url = f"http://{zeroconf_info['ip']}:{zeroconf_info['port']}{zeroconf_info['path']}?action=getInfo"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status != 200:
                            self.logger.warning("Failed to get device info: %s", response.status)
                            continue
                        device_info = await response.json()
                
                self.logger.info("📡 Found device: %s %s", 
                    device_info.get("brandDisplayName"), 
                    device_info.get("modelDisplayName"))
                
                # Step 2: Authenticate with device using Zeroconf DH
                device_url = f"http://{zeroconf_info['ip']}:{zeroconf_info['port']}{zeroconf_info['path']}"
                
                async with aiohttp.ClientSession() as session:
                    success = await authenticate_with_device(
                        session, device_url, credentials, device_info, self.logger
                    )
                
                if success:
                    self.logger.info("✅ Successfully activated %s via Zeroconf!", zeroconf_info["name"])
                    
                    # Wait a bit and check if device appears in Spotify API
                    await asyncio.sleep(2)
                    devices_data = await self._spotify_provider._get_data("me/player/devices")
                    devices = devices_data.get("devices", [])
                    
                    for device in devices:
                        if device.get("id") == device_id:
                            self._device_cache[device_id] = device
                            self.logger.info("✅ Device now visible in Spotify API!")
                            break
                    else:
                        # Device activated but not yet in API, create entry
                        self._device_cache[device_id] = {
                            "id": device_id,
                            "name": zeroconf_info["name"],
                            "type": "Speaker",
                            "is_active": True,
                            "is_restricted": False,
                            "volume_percent": 50
                        }
                else:
                    self.logger.warning("Zeroconf auth failed for %s, creating minimal entry", zeroconf_info["name"])
                    self._device_cache[device_id] = {
                        "id": device_id,
                        "name": zeroconf_info["name"],
                        "type": "Speaker",
                        "is_active": False,
                        "is_restricted": False,
                        "volume_percent": 50,
                        "zeroconf_ip": zeroconf_info["ip"],
                        "zeroconf_port": zeroconf_info["port"],
                        "zeroconf_path": zeroconf_info["path"]
                    }
                
            except asyncio.TimeoutError:
                self.logger.warning("Timeout connecting to %s", zeroconf_info["name"])
                self._device_cache[device_id] = {
                    "id": device_id,
                    "name": zeroconf_info["name"],
                    "type": "Speaker",
                    "is_active": False,
                    "is_restricted": False,
                    "volume_percent": 50,
                    "zeroconf_ip": zeroconf_info["ip"],
                    "zeroconf_port": zeroconf_info["port"],
                    "zeroconf_path": zeroconf_info["path"]
                }
            except Exception as e:
                self.logger.error("Failed to activate %s: %s", zeroconf_info["name"], e)
                self._device_cache[device_id] = {
                    "id": device_id,
                    "name": zeroconf_info["name"],
                    "type": "Speaker",
                    "is_active": False,
                    "is_restricted": False,
                    "volume_percent": 50,
                    "zeroconf_ip": zeroconf_info["ip"],
                    "zeroconf_port": zeroconf_info["port"],
                    "zeroconf_path": zeroconf_info["path"]
                }

    async def _load_librespot_credentials(self) -> dict[str, Any] | None:
        """Load librespot credentials from cache."""
        try:
            # Find the spotify cache directory
            cache_base = self.mass.cache_path
            for item in os.listdir(cache_base):
                if item.startswith("spotify-"):
                    creds_file = os.path.join(cache_base, item, "credentials.json")
                    if os.path.exists(creds_file):
                        with open(creds_file, 'r') as f:
                            return json.load(f)
            
            self.logger.warning("No librespot credentials file found in cache")
            return None
        except Exception as e:
            self.logger.error("Failed to load librespot credentials: %s", e)
            return None

    async def _periodic_discovery(self) -> None:
        """Periodically discover new Spotify Connect devices."""
        while True:
            try:
                await asyncio.sleep(DEVICE_DISCOVERY_INTERVAL)
                if self._spotify_provider:
                    self.logger.debug("Running periodic device discovery...")
                    await self.discover_players()
            except asyncio.CancelledError:
                self.logger.debug("Periodic discovery task cancelled")
                break
            except Exception as err:
                self.logger.warning("Error in periodic discovery: %s", err)

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
