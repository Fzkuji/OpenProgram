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


MAX_MSG_CHARS = 1800  # Discord caps at 2000; leave headroom


class DiscordChannel(Channel):
    platform_id = "discord"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.channels import accounts as _accounts
        creds = _accounts.load_credentials("discord", account_id)
        token = creds.get("bot_token")
        if not token:
            raise RuntimeError(
                f"Discord account {account_id!r} has no bot_token. "
                f"Run `openprogram channels accounts set-token discord "
                f"--account {account_id}`."
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
            if not text:
                return
            snippet = text[:60] + ("..." if len(text) > 60 else "")
            print(f"[{tag}] <{msg.author}> {snippet}")
            from openprogram.channels._conversation import dispatch_inbound
            peer_kind = "direct" if msg.guild is None else "channel"
            # Scoped peer id: channel_id + user_id so a shared channel
            # and a DM keep distinct sessions.
            scoped_id = f"{msg.channel.id}_{msg.author.id}"
            reply_text = await asyncio.to_thread(
                dispatch_inbound,
                channel="discord",
                account_id=self.account_id,
                peer_kind=peer_kind,
                peer_id=scoped_id,
                user_text=text,
                user_display=str(msg.author),
            )
            for chunk in _chunk(reply_text, MAX_MSG_CHARS):
                try:
                    await msg.channel.send(chunk)
                except Exception as e:  # noqa: BLE001
                    print(f"[{tag}] send failed: {e}")
                    return

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


def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]
