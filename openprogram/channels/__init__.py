"""Chat-channel bot integrations.

Config is captured by ``openprogram config channels`` (see
``setup_wizard.run_channels_section``). This module provides the
runtime: per-platform bot loops that pull user messages from a
messaging platform, run them through the chat runtime, and send the
reply back.

Only Telegram has a working implementation in this pass. Discord and
Slack raise ``NotImplementedError`` with a pointer so users know
exactly what's missing instead of seeing silent skips.
"""
from __future__ import annotations

from typing import Any

from openprogram.channels.base import Channel
from openprogram.channels.telegram import TelegramChannel
from openprogram.channels.discord import DiscordChannel
from openprogram.channels.slack import SlackChannel


CHANNEL_CLASSES: dict[str, type[Channel]] = {
    "telegram": TelegramChannel,
    "discord": DiscordChannel,
    "slack": SlackChannel,
}


def list_channels_status() -> list[dict[str, Any]]:
    """Return [{platform, enabled, configured, implemented}, ...]."""
    from openprogram.setup_wizard import _read_config
    cfg = _read_config()
    channels = cfg.get("channels", {}) or {}
    out: list[dict[str, Any]] = []
    for pid, entry in channels.items():
        if not isinstance(entry, dict):
            continue
        env_name = entry.get("api_key_env") or ""
        import os
        have_key = bool(
            (cfg.get("api_keys", {}) or {}).get(env_name)
            or os.environ.get(env_name)
        )
        out.append({
            "platform": pid,
            "enabled": bool(entry.get("enabled")),
            "configured": have_key,
            "implemented": pid in CHANNEL_CLASSES,
            "env": env_name,
        })
    return out


def list_enabled_platforms() -> list[str]:
    return [row["platform"] for row in list_channels_status() if row["enabled"]]


def build_channel(platform_id: str) -> Channel | None:
    cls = CHANNEL_CLASSES.get(platform_id)
    if cls is None:
        return None
    return cls()


__all__ = [
    "Channel",
    "CHANNEL_CLASSES",
    "list_channels_status",
    "list_enabled_platforms",
    "build_channel",
]
