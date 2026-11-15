"""Discord bot channel via ``discord.py`` (Gateway WebSocket).

Multi-account aware: each ``DiscordChannel(account_id="work")`` reads
its own bot_token from
``channels/discord/accounts/<account_id>/credentials.json``. Inbound
messages route via the binding table.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from openprogram.interfaces.channels.base import Channel

_log = logging.getLogger(__name__)


class DiscordChannel(Channel):
    platform_id = "discord"

    def peer_id_for(self, ch_msg) -> str:
        # Scoped peer id: channel_id + user_id so a shared channel and a
        # DM keep distinct sessions. _transport splits on "_" to recover
        # the channel_id for outbound sends.
        return f"{ch_msg.chat_id}_{ch_msg.user_id}"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.host.security.safe_http import require_active_sdk_transport

        require_active_sdk_transport(
            "channel.discord.gateway_sdk", "https://discord.com"
        )
        from openprogram.interfaces.channels import accounts as _accounts
        creds = _accounts.load_credentials("discord", account_id)
        token = creds.get("bot_token")
        if not token:
            raise RuntimeError(
                f"Discord account {account_id!r} has no bot_token. "
                f"Run `openprogram channels accounts login discord "
                f"--id {account_id}`."
            )
        try:
            import discord  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Discord channel requires `discord.py`. "
                "Reinstall the complete OpenProgram release."
            ) from e
        self.account_id = account_id
        self.token = token

    def _channel_message(self, msg, me):
        """A discord.Message as a ChannelMessage, or None when it has nothing to read."""
        text = (msg.content or "").strip()
        mentioned = False
        if me is not None:
            mentioned = me in (msg.mentions or [])
            for token in (f"<@{me.id}>", f"<@!{me.id}>"):
                if token in text:
                    mentioned = True
                    text = text.replace(token, "").strip()

        # Parse discord.Message → ChannelMessage.
        from openprogram.interfaces.channels._message import Attachment, ChannelMessage
        attachments = tuple(
            Attachment(
                name=str(a.filename or "file"),
                mime=str(a.content_type or ""),
                url=str(a.url or ""),
                size=int(a.size or 0),
            )
            for a in (msg.attachments or [])
        )
        if not text and not attachments:
            return None
        # 被回复消息: gateway 会把 referenced message 放进
        # reference.resolved (可能为 None — 太旧 / 已删).
        ref = getattr(msg, "reference", None)
        resolved = getattr(ref, "resolved", None) if ref else None
        quoted_text = str(getattr(resolved, "content", "") or "")
        if me is not None and getattr(resolved, "author", None) == me:
            mentioned = True
        return ChannelMessage(
            text=text,
            chat_id=str(msg.channel.id),
            user_id=str(msg.author.id),
            user_display=str(msg.author),
            message_id=str(msg.id),
            chat_type="direct" if msg.guild is None else "channel",
            ts=float(msg.created_at.timestamp()) if msg.created_at else 0.0,
            reply_to_id=(
                str(ref.message_id) if ref and ref.message_id else ""
            ),
            quoted_text=quoted_text,
            thread_id=(
                str(msg.channel.id) if getattr(msg.channel, "type", None)
                and "thread" in str(msg.channel.type).lower() else ""
            ),
            parent_chat_id=str(getattr(msg.channel, "parent_id", "") or ""),
            attachments=attachments,
            mentioned=mentioned,
            from_bot=bool(msg.author.bot),
        )

    def run(self, stop: threading.Event) -> None:
        asyncio.run(self._run_async(stop))

    async def _run_async(self, stop: threading.Event) -> None:
        from openprogram.host.security.safe_http import require_active_sdk_transport

        require_active_sdk_transport(
            "channel.discord.gateway_sdk", "https://discord.com"
        )
        import discord  # type: ignore

        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        client = discord.Client(intents=intents)
        tag = f"discord:{self.account_id}"

        @client.event
        async def on_ready() -> None:
            who = client.user
            print(f"[{tag}] logged in as {who} — ctrl+c to stop")  # noqa: T201 - terminal status

        @client.event
        async def on_message(msg) -> None:  # type: ignore[no-redef]
            if msg.author == client.user:
                return
            ch_msg = self._channel_message(msg, client.user)
            if ch_msg is None:
                return

            # handle_inbound spawns its own daemon thread and returns
            # immediately — safe to call from the gateway event loop.
            self.handle_inbound(ch_msg)

        async def _watch_stop() -> None:
            while not stop.is_set():
                await asyncio.sleep(0.5)
            print(f"[{tag}] stop signal received")  # noqa: T201 - terminal status
            await client.close()

        watcher = asyncio.create_task(_watch_stop())
        try:
            await client.start(self.token)
        except discord.LoginFailure:
            _log.error("[%s] login failed — check the bot token", tag)
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
