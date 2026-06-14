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


MAX_MSG_CHARS = 3900


class SlackChannel(Channel):
    platform_id = "slack"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.channels import accounts as _accounts
        creds = _accounts.load_credentials("slack", account_id)
        bot_token = creds.get("bot_token")
        app_token = creds.get("app_token")
        if not bot_token or not app_token:
            raise RuntimeError(
                f"Slack account {account_id!r} needs both bot_token "
                f"(xoxb-...) and app_token (xapp-...). Run "
                f"`openprogram channels accounts set-token slack "
                f"--account {account_id}`."
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

            snippet = ch_msg.text[:60] + ("..." if len(ch_msg.text) > 60 else "")
            print(f"[{tag}] <{ch_msg.user_display}> {snippet}")

            from openprogram.channels._conversation import dispatch_inbound
            scoped_id = f"{ch_msg.chat_id}_{ch_msg.user_id}"

            def _run_turn() -> None:
                reply_text = dispatch_inbound(
                    channel="slack",
                    account_id=self.account_id,
                    peer_kind=ch_msg.chat_type,
                    peer_id=scoped_id,
                    user_text=ch_msg.text,
                    user_display=ch_msg.user_display or scoped_id,
                    progress_stream=True,
                )
                # progress_stream=True 走通时 dispatch_inbound 内部已经把
                # reply edit 进占位消息, 返回 None. 降级路径返回字符串, 走
                # 旧 SDK chat_postMessage 路径.
                if reply_text is not None:
                    for chunk in _chunk(reply_text, MAX_MSG_CHARS):
                        try:
                            web.chat_postMessage(channel=channel_id, text=chunk)
                        except Exception as e:  # noqa: BLE001
                            print(f"[{tag}] send failed: {e}")
                            return

            # Per-message thread (mirrors discord/telegram/wechat): a
            # function pausing on runtime.ask must not block the socket-mode
            # listener, or the user's /answer reply never gets handled.
            threading.Thread(target=_run_turn, daemon=True).start()

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


def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]
