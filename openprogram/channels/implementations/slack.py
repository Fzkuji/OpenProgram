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

        try:
            me = web.auth_test()
        except Exception as e:  # noqa: BLE001
            # invalid_auth 是永久失败 — 正常 return, run_forever 不重启;
            # 网络类异常继续往上抛, 走退避重连.
            if "invalid_auth" in str(e) or "not_authed" in str(e):
                print(f"[{tag}] auth failed — check bot/app tokens; "
                      f"re-run `openprogram channels accounts login slack`")
                return
            raise
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
            # subtype None = 普通消息, "file_share" = 带附件的消息;
            # 其余 subtype (joined / edited / bot_message ...) 忽略.
            if event.get("subtype") not in (None, "file_share"):
                return
            if event.get("user") == my_id:
                return
            text = (event.get("text") or "").strip()
            channel_id = event.get("channel")
            user = event.get("user")

            # Parse slack event dict → ChannelMessage.
            from openprogram.channels._message import Attachment, ChannelMessage
            attachments = tuple(
                Attachment(
                    name=str(f.get("name") or "file"),
                    mime=str(f.get("mimetype") or ""),
                    url=str(f.get("url_private_download")
                            or f.get("url_private") or ""),
                    headers=(("Authorization", f"Bearer {self.bot_token}"),),
                    size=int(f.get("size") or 0),
                )
                for f in (event.get("files") or [])
            )
            if not text and not attachments:
                return

            # Thread 回复: 拉一次父消息文本作为 quoted 块 (thread_ts ==
            # ts 的是父消息本身, 不算回复).
            quoted_text = ""
            thread_ts = str(event.get("thread_ts") or "")
            if thread_ts and thread_ts != str(event.get("ts") or ""):
                try:
                    parent = web.conversations_replies(
                        channel=channel_id, ts=thread_ts, limit=1,
                    )
                    msgs = parent.get("messages") or []
                    if msgs:
                        quoted_text = str(msgs[0].get("text") or "")
                except Exception:  # noqa: BLE001
                    pass

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
                quoted_text=quoted_text,
                thread_id=thread_ts,
                attachments=attachments,
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
