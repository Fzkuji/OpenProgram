"""共享底层 — 把消息字节送到 platform server, 一份实现给两个入口共用.

入口 A (``outbound.send``): 无状态、一次性、不需要 worker 进程在跑.
  agentic-programming 范式 + cron 脚本 + jupyter 实验用这条.

入口 B (``Channel.send_text`` / ``Channel.edit_text``): 长期挂着的
  adapter 实例, 保留 message_id 可以后续 edit. dispatcher 流式回复
  + progress streaming 用这条.

这两条入口走的"如何用 HTTP 把字节送到 platform" 是同一份代码 —— 都
调到 :func:`post_message` / :func:`patch_message`. 之前的设计这层
代码在 outbound.py 和各 adapter 里复制了 5 遍 (chunking / credentials
加载 / HTTP 调用 / 错误处理), 现在合并成一份.

post_message 返回 platform-native ``message_id`` 字符串供入口 B 使用,
入口 A 用 :func:`send` 时直接丢弃即可. ``patch_message`` 在 WeChat 上
固定返回 ``False`` (iLink API 不支持编辑已发消息).
"""
from __future__ import annotations

import base64
import random
import uuid
from typing import Optional

import requests

from openprogram.channels import accounts as _accounts


# Platform-specific message size caps (字符数, 留 headroom).
# Telegram 4096, Slack 40000, Discord 2000, WeChat 由 iLink 服务端控制.
MAX_CHARS: dict[str, int] = {
    "telegram": 4000,
    "slack":    39000,
    "discord":  1800,
    "wechat":   1800,
}

# Telegram message_id 是数字, Discord/Slack 是字符串. 统一返回 str.
# Slack 的 "ts" 实际上是 "1234567890.123456" 形式的时间戳字符串, 但
# chat.update 时也用同样的 ts 作 message identifier, 没问题.


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def post_message(
    platform: str,
    account_id: str,
    target: str,
    text: str,
) -> Optional[str]:
    """发一条消息. 返回 platform-native message_id 字符串, 或 None 表示
    失败 / platform 不返回可用 id (WeChat 这种).

    ``target`` 字符串语义按 platform 不同:
      - telegram: chat_id (数字字符串或 username)
      - discord:  "{channel_id}_{user_id}", 用前半部分
      - slack:    "{channel_id}_{user_id}", 用前半部分
      - wechat:   iLink user_id

    长文本自动按平台 chunk 上限切分, 顺序发送; 返回 **最后一条** 的
    message_id (大多数用例是用最后一条做 edit 起点).
    """
    if not text:
        return None
    sender = _POSTERS.get(platform)
    if sender is None:
        print(f"[transport] unknown platform {platform!r}")
        return None
    limit = MAX_CHARS.get(platform, 1800)
    chunks = _chunk(text, limit)
    last_id: Optional[str] = None
    for chunk in chunks:
        mid = sender(account_id, target, chunk)
        if mid is None:
            # 中途失败也保留之前发出去的 message_id, 但报失败.
            return None
        last_id = mid
    return last_id


def patch_message(
    platform: str,
    account_id: str,
    target: str,
    message_id: str,
    text: str,
) -> bool:
    """改一条已发出去的消息. 返回 True/False.

    WeChat 不支持 edit (iLink API 没有这个接口), 固定返回 False —
    调用方应该用 ``post_message`` 发新消息代替 (或保留旧消息不动).
    """
    if not text:
        return False
    patcher = _PATCHERS.get(platform)
    if patcher is None:
        return False
    return patcher(account_id, target, message_id, text)


def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _post_telegram(account_id: str, chat_id: str, text: str) -> Optional[str]:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        print(f"[transport.telegram] account {account_id} has no bot_token")
        return None
    try:
        chat_id_val: object = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id_val, "text": text},
            timeout=10,
        )
        if not r.ok:
            print(f"[transport.telegram] HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        if not data.get("ok"):
            print(f"[transport.telegram] {data.get('description','?')[:200]}")
            return None
        result = data.get("result") or {}
        mid = result.get("message_id")
        return str(mid) if mid is not None else None
    except Exception as e:  # noqa: BLE001
        print(f"[transport.telegram] {type(e).__name__}: {e}")
        return None


def _patch_telegram(
    account_id: str, chat_id: str, message_id: str, text: str,
) -> bool:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return False
    try:
        chat_id_val: object = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
        msg_id_val: object = int(message_id) if message_id.isdigit() else message_id
        r = requests.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id_val, "message_id": msg_id_val, "text": text},
            timeout=10,
        )
        if not r.ok:
            print(f"[transport.telegram] edit HTTP {r.status_code}: {r.text[:200]}")
            return False
        data = r.json()
        if not data.get("ok"):
            # Telegram 在文本没变时会回 "message is not modified", 不算错.
            desc = data.get("description", "")
            if "not modified" in desc.lower():
                return True
            print(f"[transport.telegram] edit failed: {desc[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[transport.telegram] edit {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Discord — scoped_user_id 是 "{channel_id}_{user_id}", 取前半
# ---------------------------------------------------------------------------

def _discord_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "OpenProgram (https://github.com/Fzkuji/OpenProgram, 0.1)",
    }


def _post_discord(account_id: str, scoped_user_id: str, text: str) -> Optional[str]:
    creds = _accounts.load_credentials("discord", account_id)
    token = creds.get("bot_token")
    if not token:
        print(f"[transport.discord] account {account_id} has no bot_token")
        return None
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        print(f"[transport.discord] malformed user id {scoped_user_id!r}")
        return None
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=_discord_headers(token),
            json={"content": text},
            timeout=10,
        )
        if not r.ok:
            print(f"[transport.discord] HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        return str(data.get("id")) if data.get("id") else None
    except Exception as e:  # noqa: BLE001
        print(f"[transport.discord] {type(e).__name__}: {e}")
        return None


def _patch_discord(
    account_id: str, scoped_user_id: str, message_id: str, text: str,
) -> bool:
    creds = _accounts.load_credentials("discord", account_id)
    token = creds.get("bot_token")
    if not token:
        return False
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return False
    try:
        r = requests.patch(
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
            headers=_discord_headers(token),
            json={"content": text},
            timeout=10,
        )
        if not r.ok:
            print(f"[transport.discord] edit HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[transport.discord] edit {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Slack — scoped_user_id 同 Discord. message_id 是 ts 字段.
# ---------------------------------------------------------------------------

def _slack_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _post_slack(account_id: str, scoped_user_id: str, text: str) -> Optional[str]:
    creds = _accounts.load_credentials("slack", account_id)
    token = creds.get("bot_token")
    if not token:
        print(f"[transport.slack] account {account_id} has no bot_token")
        return None
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        print(f"[transport.slack] malformed user id {scoped_user_id!r}")
        return None
    try:
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers=_slack_headers(token),
            json={"channel": channel_id, "text": text},
            timeout=10,
        )
        data = r.json() if r.ok else {}
        if not data.get("ok"):
            err = data.get("error") or r.text[:200]
            print(f"[transport.slack] {err}")
            return None
        ts = data.get("ts")
        return str(ts) if ts else None
    except Exception as e:  # noqa: BLE001
        print(f"[transport.slack] {type(e).__name__}: {e}")
        return None


def _patch_slack(
    account_id: str, scoped_user_id: str, ts: str, text: str,
) -> bool:
    creds = _accounts.load_credentials("slack", account_id)
    token = creds.get("bot_token")
    if not token:
        return False
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return False
    try:
        r = requests.post(
            "https://slack.com/api/chat.update",
            headers=_slack_headers(token),
            json={"channel": channel_id, "ts": ts, "text": text},
            timeout=10,
        )
        data = r.json() if r.ok else {}
        if not data.get("ok"):
            err = data.get("error") or r.text[:200]
            print(f"[transport.slack] edit failed: {err}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[transport.slack] edit {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# WeChat — iLink. Edit 不支持.
# ---------------------------------------------------------------------------

def _make_wechat_uin() -> str:
    """Stable-per-process X-WECHAT-UIN the iLink server expects.

    复制自 channels.wechat 私有函数 — 在这里维护一份, 避免 transport
    反向依赖 wechat adapter 的私有 API. wechat adapter 自己用的那一份
    保留, 两边逻辑一样.
    """
    uin = random.getrandbits(32)
    decimal = str(uin)
    return base64.b64encode(decimal.encode("ascii")).decode("ascii")


def _post_wechat(account_id: str, user_id: str, text: str) -> Optional[str]:
    creds = _accounts.load_credentials("wechat", account_id)
    bot_token = creds.get("bot_token") or ""
    bot_id = creds.get("ilink_bot_id") or ""
    base = creds.get("baseurl") or "https://ilinkai.weixin.qq.com"
    if not bot_token or not bot_id:
        print(f"[transport.wechat] account {account_id} not logged in")
        return None
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
            print(f"[transport.wechat] {data.get('errmsg','?')[:200]}")
            return None
        # iLink 不返回稳定的 message_id, 入口 B 在 wechat 上 edit 也
        # 不支持. 但 outbound.send 仍需要区分"发出去了" vs "失败" — 用
        # 空字符串作 sentinel: ``is not None`` 为真表示发送成功, 但拿这
        # 个值去 patch_message 会被识别成空 id 而失败 (跟 wechat 本来
        # 就不支持 edit 一致).
        return ""
    except Exception as e:  # noqa: BLE001
        print(f"[transport.wechat] {type(e).__name__}: {e}")
        return None


# WeChat patch 不实现, _PATCHERS 里就不放 wechat 项, 自动走 None 分支
# 返回 False.


# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------

_POSTERS = {
    "telegram": _post_telegram,
    "discord":  _post_discord,
    "slack":    _post_slack,
    "wechat":   _post_wechat,
}

_PATCHERS = {
    "telegram": _patch_telegram,
    "discord":  _patch_discord,
    "slack":    _patch_slack,
    # wechat: 不支持 edit, 缺这一项 → patch_message 返回 False
}
