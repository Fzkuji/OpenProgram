"""Slack bot channel via Socket Mode (``slack_sdk``).

Why Socket Mode (vs the Events API): Socket Mode opens a WebSocket
from the bot to Slack, so it works behind NAT / without a public
URL. Events API needs a publicly reachable HTTPS endpoint — fine
for deployed bots, awkward for a dev-tool user running locally.

Setup prerequisites the user does once at https://api.slack.com/apps:
    1. Create app, enable Socket Mode, generate an App-Level token
       (starts ``xapp-``) with ``connections:write``
    2. Enable the ``Bot Token Scopes`` you need (``chat:write``,
       ``app_mentions:read`` at minimum) and install the app to a
       workspace to get a Bot User OAuth Token (starts ``xoxb-``)
    3. Subscribe to the ``message.im`` event (and optionally
       ``app_mention``) under Event Subscriptions

Config slots in ``channels.slack``:
    api_key_env     env var holding the bot token (default
                    SLACK_BOT_TOKEN, xoxb-...)
    app_token_env   env var holding the app-level token (default
                    SLACK_APP_TOKEN, xapp-...)
"""
from __future__ import annotations

import os
import threading
from typing import Any

from openprogram.channels.base import Channel


MAX_MSG_CHARS = 3900  # Slack caps around 4000 but we stay conservative


class SlackChannel(Channel):
    platform_id = "slack"

    def __init__(self) -> None:
        from openprogram.setup_wizard import _read_config
        cfg = _read_config()
        ch = (cfg.get("channels", {}) or {}).get("slack", {}) or {}

        bot_env = ch.get("api_key_env") or "SLACK_BOT_TOKEN"
        app_env = ch.get("app_token_env") or "SLACK_APP_TOKEN"

        bot_token = (
            os.environ.get(bot_env)
            or (cfg.get("api_keys", {}) or {}).get(bot_env)
        )
        app_token = (
            os.environ.get(app_env)
            or (cfg.get("api_keys", {}) or {}).get(app_env)
        )
        if not bot_token:
            raise RuntimeError(
                f"Slack channel: missing bot token. Set ${bot_env} or re-run "
                f"`openprogram config channels`."
            )
        if not app_token:
            raise RuntimeError(
                f"Slack channel: missing app-level token. Set ${app_env} "
                f"(Socket Mode uses xapp-... tokens with connections:write)."
            )
        try:
            import slack_sdk  # type: ignore  # noqa: F401
            from slack_sdk.socket_mode import SocketModeClient  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Slack channel requires `slack_sdk`. "
                "Install with: pip install slack_sdk"
            ) from e
        self.bot_token = bot_token
        self.app_token = app_token

    def run(self, stop: threading.Event) -> None:
        from slack_sdk.web import WebClient  # type: ignore
        from slack_sdk.socket_mode import SocketModeClient  # type: ignore
        from slack_sdk.socket_mode.request import SocketModeRequest  # type: ignore
        from slack_sdk.socket_mode.response import SocketModeResponse  # type: ignore

        rt = _get_chat_runtime_or_die()
        web = WebClient(token=self.bot_token)
        client = SocketModeClient(app_token=self.app_token, web_client=web)

        # Track our own bot user id so we can skip our own messages.
        me = web.auth_test()
        my_id = me.get("user_id")
        print(f"[slack] connected as {me.get('user')} "
              f"(model={getattr(rt, 'model', '?')}) — ctrl+c to stop")

        def _handle(_: "SocketModeClient", req: "SocketModeRequest") -> None:
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
            if req.type != "events_api":
                return
            event = (req.payload or {}).get("event", {})
            etype = event.get("type")
            if etype not in ("message", "app_mention"):
                return
            # Filter self-messages, bot_messages, message_changed, etc.
            if event.get("subtype") is not None:
                return
            if event.get("user") == my_id:
                return
            text = (event.get("text") or "").strip()
            if not text:
                return
            channel_id = event.get("channel")
            snippet = text[:60] + ("..." if len(text) > 60 else "")
            user = event.get("user")
            print(f"[slack] <{user}> {snippet}")
            # History keyed by (channel, user) so a shared channel
            # and a DM have distinct memories.
            from openprogram.channels._conversation import turn_with_history
            user_id = f"{channel_id}_{user}"
            reply_text = turn_with_history(
                platform="slack",
                user_id=user_id,
                user_text=text,
                rt=rt,
                user_display=user or user_id,
            )
            for chunk in _chunk(reply_text, MAX_MSG_CHARS):
                try:
                    web.chat_postMessage(channel=channel_id, text=chunk)
                except Exception as e:  # noqa: BLE001
                    print(f"[slack] send failed: {e}")
                    return

        client.socket_mode_request_listeners.append(_handle)
        client.connect()

        try:
            while not stop.is_set():
                stop.wait(0.5)
        finally:
            print("[slack] disconnecting")
            try:
                client.disconnect()
            except Exception:
                pass


def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _get_chat_runtime_or_die():
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
