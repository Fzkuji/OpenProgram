"""Discord bot channel via ``discord.py`` (Gateway WebSocket).

Multi-account aware: each ``DiscordChannel(account_id="work")`` reads
its own bot_token from
``channels/discord/accounts/<account_id>/credentials.json``. Inbound
messages route via the binding table.
"""
from __future__ import annotations

import asyncio
import threading

from openprogram.channels.base import Channel


class DiscordChannel(Channel):
    platform_id = "discord"

    def peer_id_for(self, ch_msg) -> str:
        # Scoped peer id: channel_id + user_id so a shared channel and a
        # DM keep distinct sessions. _transport splits on "_" to recover
        # the channel_id for outbound sends.
        return f"{ch_msg.chat_id}_{ch_msg.user_id}"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.channels import accounts as _accounts
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
                "`pip install openprogram[channels]`."
            ) from e
        self.account_id = account_id
        self.token = token

    def run(self, stop: threading.Event) -> None:
        asyncio.run(self._run_async(stop))

    async def _run_async(self, stop: threading.Event) -> None:
        import discord  # type: ignore

        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        client = discord.Client(intents=intents)
        tag = f"discord:{self.account_id}"

        @client.event
        async def on_ready() -> None:
            who = client.user
            print(f"[{tag}] logged in as {who} — ctrl+c to stop")

        @client.event
        async def on_message(msg) -> None:  # type: ignore[no-redef]
            if msg.author.bot or msg.author == client.user:
                return
            text = (msg.content or "").strip()

            # Parse discord.Message → ChannelMessage.
            from openprogram.channels._message import Attachment, ChannelMessage
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
                return
            # 被回复消息: gateway 会把 referenced message 放进
            # reference.resolved (可能为 None — 太旧 / 已删).
            ref = getattr(msg, "reference", None)
            resolved = getattr(ref, "resolved", None) if ref else None
            quoted_text = str(getattr(resolved, "content", "") or "")
            ch_msg = ChannelMessage(
                text=text,
                chat_id=str(msg.channel.id),
                user_id=str(msg.author.id),
                user_display=str(msg.author),
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
                attachments=attachments,
            )

            # handle_inbound spawns its own daemon thread and returns
            # immediately — safe to call from the gateway event loop.
            self.handle_inbound(ch_msg)

        async def _watch_stop() -> None:
            while not stop.is_set():
                await asyncio.sleep(0.5)
            print(f"[{tag}] stop signal received")
            await client.close()

        watcher = asyncio.create_task(_watch_stop())
        try:
            await client.start(self.token)
        except discord.LoginFailure:
            print(f"[{tag}] login failed — check the bot token")
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
