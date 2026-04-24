"""Chat-channel registry.

A *channel* is a platform (wechat, telegram, discord, slack).
A *channel account* is one specific bot login of that platform — e.g.
a single Telegram bot token, one WeChat QR-scan session.

Each channel class takes ``account_id`` in ``__init__`` and reads
its credentials from
``<state>/channels/<channel>/accounts/<account_id>/credentials.json``.

This module exposes the class registry + ``list_status()``, which
walks every configured account on disk and reports whether it's
enabled / configured / has an implementation. Used by the worker
runner and the Web UI.
"""
from __future__ import annotations

from typing import Any

from openprogram.channels.base import Channel
from openprogram.channels.telegram import TelegramChannel
from openprogram.channels.discord import DiscordChannel
from openprogram.channels.slack import SlackChannel
from openprogram.channels.wechat import WechatChannel


CHANNEL_CLASSES: dict[str, type[Channel]] = {
    "telegram": TelegramChannel,
    "discord": DiscordChannel,
    "slack": SlackChannel,
    "wechat": WechatChannel,
}


def list_status() -> list[dict[str, Any]]:
    """Every account across every channel with its runtime state.

    Row shape::

        {
          "platform": "telegram",
          "account_id": "default",
          "name": "Default",
          "enabled": True,
          "configured": True,
          "implemented": True,
        }
    """
    from openprogram.channels import accounts as _accounts
    out: list[dict[str, Any]] = []
    for channel, _cls in CHANNEL_CLASSES.items():
        for acct in _accounts.list_accounts(channel):
            out.append({
                "platform": channel,
                "account_id": acct.account_id,
                "name": acct.name,
                "enabled": _accounts.is_enabled(channel, acct.account_id),
                "configured": _accounts.is_configured(
                    channel, acct.account_id,
                ),
                "implemented": True,
            })
    return out


def build_channel(channel: str,
                  account_id: str = "default") -> Channel | None:
    cls = CHANNEL_CLASSES.get(channel)
    if cls is None:
        return None
    return cls(account_id=account_id)


# Back-compat shims ---------------------------------------------------------

def list_channels_status() -> list[dict[str, Any]]:
    return list_status()


def list_enabled_platforms() -> list[str]:
    return sorted({
        row["platform"] for row in list_status()
        if row["enabled"] and row["configured"]
    })


__all__ = [
    "Channel",
    "CHANNEL_CLASSES",
    "list_status",
    "list_channels_status",
    "list_enabled_platforms",
    "build_channel",
]
