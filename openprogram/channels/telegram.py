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
                    self._handle_update(upd)
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

        who = chat.get("username") or chat.get("title") or str(chat_id)
        snippet = text[:60] + ("..." if len(text) > 60 else "")
        print(f"[telegram:{self.account_id}] <{who}> {snippet}")

        from openprogram.channels._conversation import dispatch_inbound
        from openprogram.channels.outbound import send as _send
        reply_text = dispatch_inbound(
            channel="telegram",
            account_id=self.account_id,
            peer_kind="group" if chat.get("type") in ("group", "supergroup") else "direct",
            peer_id=str(chat_id),
            user_text=text,
            user_display=who,
        )
        _send("telegram", self.account_id, str(chat_id), reply_text)
