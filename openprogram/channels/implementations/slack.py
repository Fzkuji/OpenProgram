"""Slack bot channel via Socket Mode (``slack_sdk``).

Multi-account aware: each ``SlackChannel(account_id="work")`` reads
its own bot + app tokens from
``channels/slack/accounts/<account_id>/credentials.json``. Inbound
messages route via the binding table.

Credential keys:
    bot_token  (xoxb-...) — chat:write, app_mentions:read, ...
    app_token  (xapp-...) — connections:write (Socket Mode)
"""
from __future__ import annotations

import threading

from openprogram.channels.base import Channel


class SlackChannel(Channel):
    platform_id = "slack"

    def peer_id_for(self, ch_msg) -> str:
        # Scoped peer id: channel_id + user_id so a shared channel and a
        # DM keep distinct sessions. _transport splits on "_" to recover
        # the channel_id for outbound sends.
        return f"{ch_msg.chat_id}_{ch_msg.user_id}"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.channels import accounts as _accounts
        creds = _accounts.load_credentials("slack", account_id)
        bot_token = creds.get("bot_token")
        app_token = creds.get("app_token")
        if not bot_token or not app_token:
            raise RuntimeError(
                f"Slack account {account_id!r} needs both bot_token "
                f"(xoxb-...) and app_token (xapp-...). Run "
                f"`openprogram channels accounts login slack "
                f"--id {account_id}`."
            )
        try:
            import slack_sdk  # type: ignore  # noqa: F401
            from slack_sdk.socket_mode import SocketModeClient  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Slack channel requires `slack_sdk`. "
                "`pip install openprogram[channels]`."
            ) from e
        self.account_id = account_id
        self.bot_token = bot_token
        self.app_token = app_token

    def run(self, stop: threading.Event) -> None:
        from slack_sdk.web import WebClient  # type: ignore
        from slack_sdk.socket_mode import SocketModeClient  # type: ignore
        from slack_sdk.socket_mode.request import SocketModeRequest  # type: ignore
        from slack_sdk.socket_mode.response import SocketModeResponse  # type: ignore

        web = WebClient(token=self.bot_token)
        client = SocketModeClient(app_token=self.app_token, web_client=web)
        tag = f"slack:{self.account_id}"

        me = web.auth_test()
        my_id = me.get("user_id")
        print(f"[{tag}] connected as {me.get('user')} — ctrl+c to stop")

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
            if event.get("subtype") is not None:
                return
            if event.get("user") == my_id:
                return
            text = (event.get("text") or "").strip()
            if not text:
                return
            channel_id = event.get("channel")
            user = event.get("user")

            # Parse slack event dict → ChannelMessage (audit 缺陷 4).
            from openprogram.channels._message import ChannelMessage
            ch_msg = ChannelMessage(
                text=text,
                chat_id=str(channel_id or ""),
                user_id=str(user or ""),
                user_display=str(user or channel_id or ""),
                chat_type=(
                    "direct" if (channel_id or "").startswith("D")
                    else "channel"
                ),
                ts=float(event.get("ts") or 0),
                thread_id=str(event.get("thread_ts") or ""),
            )

            # handle_inbound spawns its own daemon thread — the socket-mode
            # listener callback returns immediately.
            self.handle_inbound(ch_msg)

        client.socket_mode_request_listeners.append(_handle)
        client.connect()
        try:
            while not stop.is_set():
                stop.wait(0.5)
        finally:
            print(f"[{tag}] disconnecting")
            try:
                client.disconnect()
            except Exception:
                pass
