"""Update channels: the names ``update.channel`` may take.

A channel maps to the git remote and ref a source checkout upgrades from.
The settings schema (``state.settings``) lists the names as the setting's
choices; ``openprogram upgrade`` (``operations.self_update``) resolves one.
"""
from __future__ import annotations

# channel name -> (git remote, ref). One built-in channel today; adding
# another is a line here plus nothing else.
CHANNELS: dict[str, tuple[str, str]] = {
    "stable": ("origin", "main"),
}

DEFAULT_CHANNEL = "stable"
CONFIG_KEY = "update.channel"

__all__ = ["CHANNELS", "CONFIG_KEY", "DEFAULT_CHANNEL"]
