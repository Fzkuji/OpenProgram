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


class TelegramChannel(Channel):
    platform_id = "telegram"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.channels import accounts as _accounts
        creds = _accounts.load_credentials("telegram", account_id)
        token = creds.get("bot_token")
        if not token:
            raise RuntimeError(
                f"Telegram account {account_id!r} has no bot_token. "
                f"Run `openprogram channels accounts login telegram "
                f"--id {account_id}`."
            )
        self.account_id = account_id
        self.token = token
        self.base = f"{TELEGRAM_API}/bot{token}"
        self.offset = 0
        # 群聊语义 — 显式账号配置 (accounts.ACCOUNT_SETTINGS), 不是隐式
        # 行为. `openprogram channels accounts set telegram <key> <value>`
        # 修改, 重启 worker 生效.
        settings = _accounts.get_settings("telegram", account_id)
        #: "shared" = 全群一个会话 (默认); "per-user" = 群内每人一个会话
        #: (peer_id 变成 "{chat_id}_{user_id}", _transport 出站取前半).
        self.group_sessions = settings.get("group_sessions", "shared")
        #: "on" = 群聊里只响应 @bot 提及或对 bot 消息的回复.
        self.require_mention = settings.get("require_mention", "off") == "on"
        # run() 里 getMe 后填充, mention 判定用.
        self.bot_username = ""
        self.bot_user_id = ""

    def peer_id_for(self, ch_msg) -> str:
        if (ch_msg.chat_type == "group"
                and self.group_sessions == "per-user"
                and ch_msg.user_id):
            return f"{ch_msg.chat_id}_{ch_msg.user_id}"
        return ch_msg.chat_id

    def run(self, stop: threading.Event) -> None:
        import requests
        me = self._get_me()
        tag = f"telegram:{self.account_id}"
        if me:
            self.bot_username = str(me.get("username") or "")
            self.bot_user_id = str(me.get("id") or "")
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
                    # parse 在 loop 里 (纯 dict 访问); dispatch 的
                    # per-message 线程派发在 base.handle_inbound.
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
        chat = msg.get("chat", {}) or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        text = (msg.get("text") or msg.get("caption") or "").strip()
        attachments = self._parse_attachments(msg)
        if not text and not attachments:
            return

        is_group = chat.get("type") in ("group", "supergroup")
        reply_to = msg.get("reply_to_message", {}) or {}

        # 群聊 @提及 门槛 (require_mention=on): 只响应 @bot 或对 bot
        # 消息的回复; 命中后把提及从文本里剥掉再进 dispatch.
        if is_group and self.require_mention:
            mention = f"@{self.bot_username}" if self.bot_username else ""
            replied_to_bot = bool(
                self.bot_user_id
                and str((reply_to.get("from") or {}).get("id") or "")
                == self.bot_user_id
            )
            if mention and mention in text:
                text = text.replace(mention, "").strip()
            elif not replied_to_bot:
                return

        # Parse platform-native msg → ChannelMessage.
        from openprogram.channels._message import ChannelMessage
        from_user = msg.get("from", {}) or {}
        ch_msg = ChannelMessage(
            text=text,
            chat_id=str(chat_id),
            user_id=str(from_user.get("id") or ""),
            user_display=(
                from_user.get("username") or chat.get("username")
                or chat.get("title") or str(chat_id)
            ),
            chat_type="group" if is_group else "direct",
            ts=float(msg.get("date") or 0),
            reply_to_id=str(reply_to.get("message_id") or ""),
            quoted_text=(
                reply_to.get("text") or reply_to.get("caption") or ""
            ),
            attachments=attachments,
        )

        self.handle_inbound(ch_msg)

    @staticmethod
    def _parse_attachments(msg: dict) -> tuple:
        """photo (取最大尺寸) 和 document → Attachment 元组. 下载时经
        getFile 解析 URL (_attachments._resolve_telegram_file)."""
        from openprogram.channels._message import Attachment
        out = []
        photos = msg.get("photo") or []
        if photos:
            biggest = photos[-1]  # Bot API 按尺寸升序
            out.append(Attachment(
                name="photo.jpg",
                mime="image/jpeg",
                file_id=str(biggest.get("file_id") or ""),
                size=int(biggest.get("file_size") or 0),
            ))
        doc = msg.get("document")
        if doc:
            out.append(Attachment(
                name=str(doc.get("file_name") or "file"),
                mime=str(doc.get("mime_type") or ""),
                file_id=str(doc.get("file_id") or ""),
                size=int(doc.get("file_size") or 0),
            ))
        return tuple(out)
