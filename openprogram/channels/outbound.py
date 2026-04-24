"""Outbound — send a text message from any process to (channel, account,
user/chat). Credentials come from ``channels.accounts`` rather than a
global config section, so multiple accounts per platform Just Work.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional

from openprogram.channels import accounts as _accounts


_MAX_MSG_CHARS = 1800


def send(channel: str, account_id: str, user_id: str, text: str) -> bool:
    """Deliver ``text`` to (channel, account_id, user_id). Returns True
    if every chunk landed. Chunks at ~1800 chars."""
    if not text:
        return True
    sender = _SENDERS.get(channel)
    if sender is None:
        print(f"[outbound] unknown channel {channel!r}")
        return False
    ok = True
    for chunk in _chunk(text, _MAX_MSG_CHARS):
        if not sender(account_id, user_id, chunk):
            ok = False
    return ok


def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _send_telegram(account_id: str, chat_id: str, text: str) -> bool:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        print(f"[outbound.telegram] account {account_id} has no bot_token")
        return False
    import requests
    try:
        chat_id_val: object = int(chat_id) if chat_id.lstrip("-").isdigit() \
            else chat_id
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id_val, "text": text},
            timeout=10,
        )
        if not r.ok:
            print(f"[outbound.telegram] HTTP {r.status_code}: "
                  f"{r.text[:200]}")
            return False
        data = r.json()
        if not data.get("ok"):
            print(f"[outbound.telegram] {data.get('description','?')[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[outbound.telegram] {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Discord — raw HTTP; user_id scheme is "<channel_id>_<user_id>"
# ---------------------------------------------------------------------------

def _send_discord(account_id: str, scoped_user_id: str, text: str) -> bool:
    creds = _accounts.load_credentials("discord", account_id)
    token = creds.get("bot_token")
    if not token:
        print(f"[outbound.discord] account {account_id} has no bot_token")
        return False
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        print(f"[outbound.discord] malformed user id {scoped_user_id!r}")
        return False
    import requests
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "OpenProgram (https://github.com/Fzkuji/OpenProgram, 0.1)",
            },
            json={"content": text},
            timeout=10,
        )
        if not r.ok:
            print(f"[outbound.discord] HTTP {r.status_code}: "
                  f"{r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[outbound.discord] {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Slack — Web API chat.postMessage, scoped user id same as Discord
# ---------------------------------------------------------------------------

def _send_slack(account_id: str, scoped_user_id: str, text: str) -> bool:
    creds = _accounts.load_credentials("slack", account_id)
    token = creds.get("bot_token")
    if not token:
        print(f"[outbound.slack] account {account_id} has no bot_token")
        return False
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        print(f"[outbound.slack] malformed user id {scoped_user_id!r}")
        return False
    import requests
    try:
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"channel": channel_id, "text": text},
            timeout=10,
        )
        data = r.json() if r.ok else {}
        if not data.get("ok"):
            err = data.get("error") or r.text[:200]
            print(f"[outbound.slack] {err}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[outbound.slack] {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# WeChat — iLink bot sendmessage
# ---------------------------------------------------------------------------

def _send_wechat(account_id: str, user_id: str, text: str) -> bool:
    creds = _accounts.load_credentials("wechat", account_id)
    bot_token = creds.get("bot_token") or ""
    bot_id = creds.get("ilink_bot_id") or ""
    base = creds.get("baseurl") or "https://ilinkai.weixin.qq.com"
    if not bot_token or not bot_id:
        print(f"[outbound.wechat] account {account_id} not logged in")
        return False
    import requests
    from openprogram.channels.wechat import _make_wechat_uin
    try:
        r = requests.post(
            f"{base}/ilink/bot/sendmessage",
            headers={
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "Authorization": f"Bearer {bot_token}",
                "X-WECHAT-UIN": _make_wechat_uin(),
            },
            json={
                "msg": {
                    "from_user_id": bot_id,
                    "to_user_id": user_id,
                    "client_id": uuid.uuid4().hex,
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                    "context_token": "",
                },
                "base_info": {},
            },
            timeout=15,
        )
        data = r.json() if r.ok else {}
        if data.get("ret", 0) != 0:
            print(f"[outbound.wechat] {data.get('errmsg','?')[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[outbound.wechat] {type(e).__name__}: {e}")
        return False


_SENDERS = {
    "telegram": _send_telegram,
    "discord":  _send_discord,
    "slack":    _send_slack,
    "wechat":   _send_wechat,
}
