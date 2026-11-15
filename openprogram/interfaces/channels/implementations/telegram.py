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

import logging
import threading
import time
from typing import Any

from openprogram.interfaces.channels.base import Channel

_log = logging.getLogger(__name__)


TELEGRAM_API = "https://api.telegram.org"
#: 401 = token 被撤销, 404 = URL 里的 token 不存在. 409 (另一个轮询者) 会过去, 继续重试.
_TOKEN_REJECTED_STATUSES = frozenset({401, 404})
#: 消息字段 → 没带文件名 / MIME 时用的默认值.
_MEDIA_FIELDS = (
    ("voice", "voice.ogg", "audio/ogg"),
    ("audio", "audio.mp3", "audio/mpeg"),
    ("video", "video.mp4", "video/mp4"),
    ("video_note", "video_note.mp4", "video/mp4"),
    ("animation", "animation.mp4", "video/mp4"),
)


class TelegramChannel(Channel):
    platform_id = "telegram"

    def __init__(self, account_id: str = "default") -> None:
        from openprogram.interfaces.channels import accounts as _accounts
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
        # 进程内去重在重启后就没了; 启动前发的消息被编辑时不能当新消息.
        self.started_at = time.time()
        # 群聊语义 — 显式账号配置 (accounts.ACCOUNT_SETTINGS), 不是隐式
        # 行为. `openprogram channels accounts set telegram <key> <value>`
        # 修改, 重启 worker 生效.
        settings = _accounts.get_settings("telegram", account_id)
        #: "shared" = 全群一个会话 (默认); "per-user" = 群内每人一个会话
        #: (peer_id 变成 "{chat_id}_{user_id}", _transport 出站取前半).
        self.group_sessions = settings.get("group_sessions", "shared")
        # 群聊 @提及 门槛在 base 的房间层 (_room), 这里只判定"是不是冲
        # 机器人说的"并剥掉提及. run() 里 getMe 后填充.
        self.bot_username = ""
        self.bot_user_id = ""

    def peer_id_for(self, ch_msg) -> str:
        if (ch_msg.chat_type == "group"
                and self.group_sessions == "per-user"
                and ch_msg.user_id):
            return f"{ch_msg.chat_id}_{ch_msg.user_id}"
        return ch_msg.chat_id

    def run(self, stop: threading.Event) -> None:
        from openprogram.host.security.safe_http import safe_client

        me = self._get_me()
        tag = f"telegram:{self.account_id}"
        if me:
            self.bot_username = str(me.get("username") or "")
            self.bot_user_id = str(me.get("id") or "")
            print(f"[{tag}] @{me.get('username','?')} online — ctrl+c to stop")  # noqa: T201 - terminal status
        else:
            _log.warning("[%s] online (identity check failed); continuing", tag)

        while not stop.is_set():
            try:
                with safe_client("channel.telegram.api") as client:
                    r = client.get(
                        f"{self.base}/getUpdates",
                        params={"offset": self.offset, "timeout": 25},
                    )
                if r.status_code in _TOKEN_REJECTED_STATUSES:
                    # 正常 return = 永久停止, 换了凭据 worker 才会重新拉起.
                    _log.error(
                        "[%s] bot token rejected (HTTP %s) — run `openprogram "
                        "channels accounts login telegram --id %s`.",
                        tag, r.status_code, self.account_id,
                    )
                    return
                data = r.json() if r.is_success else {}
                if not data.get("ok"):
                    _log.warning("[%s] API error %s", tag, r.status_code)
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
                _log.info("[%s] poll failed: %s", tag, type(e).__name__)
                time.sleep(3)

    def _get_me(self) -> dict[str, Any] | None:
        from openprogram.host.security.safe_http import safe_client

        try:
            with safe_client("channel.telegram.api") as client:
                r = client.get(f"{self.base}/getMe")
            if r.is_success and r.json().get("ok"):
                return r.json().get("result")
        except Exception:
            # None tells the caller the bot identity is unknown.
            _log.debug("telegram getMe failed", exc_info=True)
        return None

    def _handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        if not upd.get("message") and float(msg.get("date") or 0) < int(self.started_at):
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
        text, mentioned = self._strip_mention(text)
        mentioned = mentioned or bool(
            self.bot_user_id
            and str((reply_to.get("from") or {}).get("id") or "")
            == self.bot_user_id
        )

        # Parse platform-native msg → ChannelMessage.
        from openprogram.interfaces.channels._message import ChannelMessage
        from_user = msg.get("from", {}) or {}
        user_id = str(from_user.get("id") or "")
        user_name = " ".join(
            part for part in (
                str(from_user.get("first_name") or "").strip(),
                str(from_user.get("last_name") or "").strip(),
            ) if part
        )
        ch_msg = ChannelMessage(
            text=text,
            chat_id=str(chat_id),
            user_id=user_id,
            user_display=(str(from_user.get("username") or "").strip()
                          or user_name or user_id),
            message_id=str(msg.get("message_id") or ""),
            chat_type="group" if is_group else "direct",
            ts=float(msg.get("date") or 0),
            reply_to_id=str(reply_to.get("message_id") or ""),
            quoted_text=(
                reply_to.get("text") or reply_to.get("caption") or ""
            ),
            # 论坛话题: 回复发回同一个话题 (不进 General). 普通群里回复
            # 链也带 message_thread_id, 但那不是话题, 不能拿来发消息.
            thread_id=(str(msg.get("message_thread_id") or "")
                       if msg.get("is_topic_message") else ""),
            attachments=attachments,
            mentioned=mentioned,
            from_bot=bool(from_user.get("is_bot")),
        )

        self.handle_inbound(ch_msg)

    def _strip_mention(self, text: str) -> tuple[str, bool]:
        """剥掉 ``@bot`` 提及 (包括 ``/new@bot`` 这种群命令后缀), 返回
        (剩下的文本, 是否提及了本机器人)."""
        if not self.bot_username:
            return text, False
        import re
        pattern = re.compile(rf"@{re.escape(self.bot_username)}\b", re.I)
        if not pattern.search(text):
            return text, False
        return re.sub(r"[ \t]{2,}", " ", pattern.sub("", text)).strip(), True

    @staticmethod
    def _parse_attachments(msg: dict) -> tuple:
        """photo (取最大尺寸)、document 和音视频 → Attachment 元组. 下载时
        经 getFile 解析 URL (_attachments._resolve_telegram_file)."""
        from openprogram.interfaces.channels._message import Attachment
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
        for key, default_name, default_mime in _MEDIA_FIELDS:
            media = msg.get(key)
            if media:
                out.append(Attachment(
                    name=str(media.get("file_name") or default_name),
                    mime=str(media.get("mime_type") or default_mime),
                    file_id=str(media.get("file_id") or ""),
                    size=int(media.get("file_size") or 0),
                    voice=key == "voice",
                ))
        doc = msg.get("document")
        # Bot API 给 GIF 同时发 animation 和指向同一文件的 document.
        if doc and not msg.get("animation"):
            out.append(Attachment(
                name=str(doc.get("file_name") or "file"),
                mime=str(doc.get("mime_type") or ""),
                file_id=str(doc.get("file_id") or ""),
                size=int(doc.get("file_size") or 0),
            ))
        return tuple(out)
