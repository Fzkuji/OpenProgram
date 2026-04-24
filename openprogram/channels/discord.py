"""Discord bot channel via ``discord.py``.

Unlike Telegram's HTTP long-poll, Discord requires a persistent
Gateway WebSocket connection with a proper library. We use
``discord.py`` (2.x) which is the canonical Python SDK.

Bot setup prerequisites the user does ONCE in the Discord Developer
Portal:
    1. Create Application + Bot user
    2. Enable the ``MESSAGE CONTENT`` privileged intent
    3. Invite the bot to your server with scopes ``bot`` +
       ``applications.commands`` and at minimum the ``Send Messages``
       permission

``openprogram config channels`` stores the bot token (env var
``DISCORD_BOT_TOKEN`` by default); this module pulls it at runtime.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from openprogram.channels.base import Channel


MAX_MSG_CHARS = 1800  # Discord caps at 2000; leave headroom for prefixes


class DiscordChannel(Channel):
    platform_id = "discord"

    def __init__(self) -> None:
        from openprogram.setup_wizard import _read_config
        cfg = _read_config()
        ch = (cfg.get("channels", {}) or {}).get("discord", {}) or {}
        env_name = ch.get("api_key_env") or "DISCORD_BOT_TOKEN"
        token = (
            os.environ.get(env_name)
            or (cfg.get("api_keys", {}) or {}).get(env_name)
        )
        if not token:
            raise RuntimeError(
                f"Discord channel: missing token. Set ${env_name} or re-run "
                f"`openprogram config channels`."
            )
        try:
            import discord  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Discord channel requires `discord.py`. "
                "Install with: pip install discord.py"
            ) from e
        self.token = token

    def run(self, stop: threading.Event) -> None:
        # discord.py runs its own asyncio loop; we need a bridge
        # between our threading.Event (set by Ctrl+C in the runner)
        # and the library's ``close()`` coroutine. Solution: run the
        # client via asyncio.run() in this thread + have a parallel
        # task watch the stop event and trigger close.
        asyncio.run(self._run_async(stop))

    async def _run_async(self, stop: threading.Event) -> None:
        import discord  # type: ignore

        rt = _get_chat_runtime_or_die()

        intents = discord.Intents.default()
        intents.message_content = True    # privileged; must be toggled
        intents.messages = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:
            who = client.user
            print(f"[discord] logged in as {who} "
                  f"(model={getattr(rt, 'model', '?')}) — ctrl+c to stop")

        @client.event
        async def on_message(msg) -> None:  # type: ignore[no-redef]
            if msg.author.bot or msg.author == client.user:
                return
            text = (msg.content or "").strip()
            if not text:
                return
            snippet = text[:60] + ("..." if len(text) > 60 else "")
            print(f"[discord] <{msg.author}> {snippet}")
            # turn_with_history is blocking (does disk I/O + rt.exec);
            # hand it to a worker thread so the gateway heartbeat
            # keeps flowing. Key channel scope on (channel_id, user_id)
            # so each person's DM / thread gets its own history.
            from openprogram.channels._conversation import turn_with_history
            user_id = f"{msg.channel.id}_{msg.author.id}"
            reply_text = await asyncio.to_thread(
                turn_with_history,
                "discord", user_id, text, rt,
                user_display=str(msg.author),
            )
            for chunk in _chunk(reply_text, MAX_MSG_CHARS):
                try:
                    await msg.channel.send(chunk)
                except Exception as e:  # noqa: BLE001
                    print(f"[discord] send failed: {e}")
                    return

        # Stop-event watcher task: polls the threading Event and
        # triggers client.close() when set. Plain coroutine so it
        # cooperates with discord.py's loop.
        async def _watch_stop() -> None:
            while not stop.is_set():
                await asyncio.sleep(0.5)
            print("[discord] stop signal received")
            await client.close()

        watcher = asyncio.create_task(_watch_stop())
        try:
            await client.start(self.token)
        except discord.LoginFailure:
            print("[discord] login failed — check DISCORD_BOT_TOKEN")
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


def _get_chat_runtime_or_die():
    """Same helper as telegram channel — kept here to avoid circular imports."""
    from openprogram.webui import _runtime_management as rm
    rm._init_providers()
    rt = rm._chat_runtime
    if rt is None:
        raise RuntimeError(
            "No chat runtime configured. Run `openprogram setup` first."
        )
    try:
        from openprogram.setup_wizard import read_agent_prefs
        eff = read_agent_prefs().get("thinking_effort")
        if eff:
            rt.thinking_level = eff
    except Exception:
        pass
    return rt
