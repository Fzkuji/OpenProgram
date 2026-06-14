"""Telegram bot channel via the public Bot API (long-polling).

Multi-account aware: each ``TelegramChannel(account_id="work")``
reads its own bot_token from
``channels/telegram/accounts/<account_id>/credentials.json`` and
routes inbound messages via the binding table.

Protocol:
    getUpdates  long-poll incoming messages (offset = last_seen + 1)
    sendMessage reply to a chat
    getMe       used on start to confirm the token
"""
from __future__ import annotations

import threading
import time
from typing import Any

from openprogram.channels.base import Channel


TELEGRAM_API = "https://api.telegram.org"
MAX_MSG_CHARS = 4000   # Telegram caps at 4096; leave headroom


class TelegramChannel(Channel):
    platform_id = "telegram"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.channels import accounts as _accounts
        creds = _accounts.load_credentials("telegram", account_id)
        token = creds.get("bot_token")
        if not token:
            raise RuntimeError(
                f"Telegram account {account_id!r} has no bot_token. "
                f"Run `openprogram channels accounts set-token telegram "
                f"--account {account_id}`."
            )
        self.account_id = account_id
        self.token = token
        self.base = f"{TELEGRAM_API}/bot{token}"
        self.offset = 0

    def run(self, stop: threading.Event) -> None:
        import requests
        me = self._get_me()
        tag = f"telegram:{self.account_id}"
        if me:
            print(f"[{tag}] @{me.get('username','?')} online — ctrl+c to stop")
        else:
            print(f"[{tag}] online (identity check failed); continuing")

        while not stop.is_set():
            try:
                r = requests.get(
                    f"{self.base}/getUpdates",
                    params={"offset": self.offset, "timeout": 25},
                    timeout=40,
                )
                data = r.json() if r.ok else {}
                if not data.get("ok"):
                    print(f"[{tag}] API error {r.status_code}: "
                          f"{(data.get('description') or r.text)[:200]}")
                    time.sleep(5)
                    continue
                for upd in data.get("result", []):
                    self.offset = upd["update_id"] + 1
                    # Per-message thread (mirrors discord's to_thread): a
                    # function pausing on runtime.ask inside _handle_update
                    # must NOT block this poll loop — else the user's own
                    # /answer reply (fetched by this same loop) never
                    # arrives and the wait self-deadlocks.
                    threading.Thread(
                        target=self._handle_update, args=(upd,), daemon=True,
                    ).start()
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[{tag}] poll failed: {type(e).__name__}: {e}")
                time.sleep(3)

    def _get_me(self) -> dict[str, Any] | None:
        import requests
        try:
            r = requests.get(f"{self.base}/getMe", timeout=10)
            if r.ok and r.json().get("ok"):
                return r.json().get("result")
        except Exception:
            pass
        return None

    def _handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        text = msg.get("text")
        if not text:
            return
        chat = msg.get("chat", {}) or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return

        # Parse platform-native msg → ChannelMessage (audit 缺陷 4).
        from openprogram.channels._message import ChannelMessage
        from_user = msg.get("from", {}) or {}
        reply_to = msg.get("reply_to_message", {}) or {}
        ch_msg = ChannelMessage(
            text=text,
            chat_id=str(chat_id),
            user_id=str(from_user.get("id") or ""),
            user_display=(
                chat.get("username") or chat.get("title") or str(chat_id)
            ),
            chat_type=(
                "group" if chat.get("type") in ("group", "supergroup")
                else "direct"
            ),
            ts=float(msg.get("date") or 0),
            reply_to_id=str(reply_to.get("message_id") or ""),
        )

        snippet = ch_msg.text[:60] + ("..." if len(ch_msg.text) > 60 else "")
        print(f"[telegram:{self.account_id}] <{ch_msg.user_display}> {snippet}")

        from openprogram.channels._conversation import dispatch_inbound
        from openprogram.channels.outbound import send as _send
        reply_text = dispatch_inbound(
            channel="telegram",
            account_id=self.account_id,
            peer_kind=ch_msg.chat_type,
            peer_id=ch_msg.chat_id,
            user_text=ch_msg.text,
            user_display=ch_msg.user_display,
            progress_stream=True,
        )
        # progress_stream=True 时 dispatch_inbound 内部已经把 reply edit
        # 进占位消息, 返回 None 表示无需再发. 占位发送失败 / 任何降级路径
        # 会返回 reply_text 字符串, 走旧 _send 路径.
        if reply_text is not None:
            _send("telegram", self.account_id, ch_msg.chat_id, reply_text)
