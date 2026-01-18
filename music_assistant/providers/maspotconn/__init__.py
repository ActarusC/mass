"""
Spotify Connect Player Provider for Music Assistant.

This provider allows Music Assistant to control existing Spotify Connect devices
(like JBL Authentics 500) using the Spotify Web API. The audio streaming remains
direct between Spotify and the device (lossless quality preserved), while Music
Assistant acts as a remote control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .provider import MaspotconnProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return MaspotconnProvider(mass, manifest, config)


async def get_config_entries(
    mass: MusicAssistant,  # noqa: ARG001
    instance_id: str | None = None,  # noqa: ARG001
    action: str | None = None,  # noqa: ARG001
    values: dict[str, str] | None = None,  # noqa: ARG001
) -> tuple[()]:
    """
    Return Config entries to setup this provider.

    instance_id: id of an existing provider instance (None if new instance setup).
    action: [optional] action key called from config entries UI.
    values: the (intermediate) raw values for config entries sent with the action.
    """
    # No additional config needed - we reuse Spotify music provider authentication
    return ()
