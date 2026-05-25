"""共享底层 — 把消息字节送到 platform server, 一份实现给两个入口共用.

入口 A (``outbound.send``): 无状态、一次性、不需要 worker 进程在跑.
  agentic-programming 范式 + cron 脚本 + jupyter 实验用这条.

入口 B (``Channel.send_text`` / ``Channel.edit_text``): 长期挂着的
  adapter 实例, 保留 message_id 可以后续 edit. dispatcher 流式回复
  + progress streaming 用这条.

这两条入口走的"如何用 HTTP 把字节送到 platform" 是同一份代码 —— 都
调到 :func:`post_message` / :func:`patch_message`.

post_message 返回 :class:`SendResult` (含 ok / message_id /
error_kind / retryable), 入口 A 用 :func:`send` 只看 ``ok``, 入口 B
拿 ``message_id`` 构造 MessageHandle. patch_message 同样.

``error_kind`` 枚举:

  ``auth``         — 凭据问题: token 错、过期、bot 被踢
  ``rate_limit``   — 速率限制 (Telegram 429 / Discord 429 / Slack ratelimited)
  ``bad_target``   — 收信人不对: chat_id 错、channel 不存在、bot 没权限
  ``network``      — 连不上 / 超时 / SSL 错
  ``not_supported``— 平台不支持该操作 (主要给 WeChat edit 用)
  ``unknown``      — 其他, 看 error_detail
"""
from __future__ import annotations

import base64
import random
import uuid
from dataclasses import dataclass, field
from typing import Optional

import requests

from openprogram.channels import accounts as _accounts


# Platform-specific message size caps (字符数, 留 headroom).
MAX_CHARS: dict[str, int] = {
    "telegram": 4000,
    "slack":    39000,
    "discord":  1800,
    "wechat":   1800,
}


@dataclass(frozen=True)
class SendResult:
    """Send / edit 操作的结构化结果.

    ``ok`` True 时 ``message_id`` 是 platform-native 字符串 (可能为空,
    比如 WeChat 不返回可用 id 但发送成功). ``ok`` False 时 ``error_kind``
    标识失败类别, ``error_detail`` 是 human-readable 详情 (一行).
    ``retryable`` True 表示瞬态失败值得重试 (网络 / rate_limit), False
    表示永久失败 (auth / bad_target / not_supported).
    """
    ok: bool
    message_id: str = ""
    error_kind: str = ""
    error_detail: str = ""
    retryable: bool = False

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls, message_id: str = "") -> "SendResult":
        return cls(ok=True, message_id=message_id)

    @classmethod
    def fail(
        cls, kind: str, detail: str = "", *, retryable: bool = False,
    ) -> "SendResult":
        return cls(ok=False, error_kind=kind, error_detail=detail, retryable=retryable)


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def post_message(
    platform: str,
    account_id: str,
    target: str,
    text: str,
) -> SendResult:
    """发一条消息. 返回 :class:`SendResult`.

    ``target`` 字符串语义按 platform 不同 (见模块 docstring).

    长文本自动按平台 chunk 上限切分, 顺序发送; 返回 **最后一条** 的
    SendResult — 中途失败立即返回当时的失败结果, 之前发出去的不撤回.
    """
    if not text:
        return SendResult.fail("bad_target", "empty text")
    sender = _POSTERS.get(platform)
    if sender is None:
        return SendResult.fail(
            "not_supported", f"unknown platform {platform!r}",
        )
    limit = MAX_CHARS.get(platform, 1800)
    chunks = _chunk(text, limit)
    last: SendResult = SendResult.fail("unknown", "no chunks sent")
    for chunk in chunks:
        last = sender(account_id, target, chunk)
        if not last.ok:
            return last
    return last


def patch_message(
    platform: str,
    account_id: str,
    target: str,
    message_id: str,
    text: str,
) -> SendResult:
    """改一条已发出去的消息. 返回 :class:`SendResult`.

    WeChat 永远返回 ``not_supported`` error (iLink API 没有 editMessage).
    调用方应该用 ``post_message`` 发新消息代替.
    """
    if not text:
        return SendResult.fail("bad_target", "empty text")
    patcher = _PATCHERS.get(platform)
    if patcher is None:
        return SendResult.fail(
            "not_supported",
            f"{platform!r} does not support editing messages",
        )
    return patcher(account_id, target, message_id, text)


def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


# ---------------------------------------------------------------------------
# 错误分类 helpers
# ---------------------------------------------------------------------------

def _classify_network_error(exc: Exception) -> SendResult:
    """request 库异常 → SendResult."""
    name = type(exc).__name__
    detail = f"{name}: {exc}"
    # 所有 requests 异常都当 network. 上层不知道更细节也没法 retry 得
    # 更聪明, 重要的是给 retryable=True.
    return SendResult.fail("network", detail, retryable=True)


def _classify_http_status(status: int, body: str) -> SendResult:
    """根据 HTTP status code + response body 给个 error_kind."""
    snippet = body[:200] if body else ""
    if status == 401 or status == 403:
        return SendResult.fail("auth", f"HTTP {status}: {snippet}")
    if status == 404:
        return SendResult.fail("bad_target", f"HTTP {status}: {snippet}")
    if status == 429:
        return SendResult.fail("rate_limit", f"HTTP {status}: {snippet}", retryable=True)
    if 500 <= status < 600:
        return SendResult.fail("network", f"HTTP {status}: {snippet}", retryable=True)
    return SendResult.fail("unknown", f"HTTP {status}: {snippet}")


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _post_telegram(account_id: str, chat_id: str, text: str) -> SendResult:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    try:
        chat_id_val: object = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id_val, "text": text},
            timeout=10,
        )
        if not r.ok:
            return _classify_http_status(r.status_code, r.text)
        data = r.json()
        if not data.get("ok"):
            desc = data.get("description", "") or ""
            kind = _telegram_kind_from_description(desc)
            return SendResult.fail(kind, desc, retryable=(kind == "rate_limit"))
        result = data.get("result") or {}
        mid = result.get("message_id")
        return SendResult.success(str(mid) if mid is not None else "")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _patch_telegram(
    account_id: str, chat_id: str, message_id: str, text: str,
) -> SendResult:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    try:
        chat_id_val: object = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
        msg_id_val: object = int(message_id) if message_id.isdigit() else message_id
        r = requests.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={"chat_id": chat_id_val, "message_id": msg_id_val, "text": text},
            timeout=10,
        )
        if not r.ok:
            return _classify_http_status(r.status_code, r.text)
        data = r.json()
        if not data.get("ok"):
            desc = data.get("description", "") or ""
            # Telegram 在文本没变时回 "message is not modified", 视为成功
            if "not modified" in desc.lower():
                return SendResult.success(message_id)
            kind = _telegram_kind_from_description(desc)
            return SendResult.fail(kind, desc, retryable=(kind == "rate_limit"))
        return SendResult.success(message_id)
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _telegram_kind_from_description(desc: str) -> str:
    """从 Telegram 业务错误描述里推断 error_kind."""
    low = desc.lower()
    if "unauthorized" in low or "bot token" in low:
        return "auth"
    if "too many requests" in low or "flood" in low:
        return "rate_limit"
    if "chat not found" in low or "bot was kicked" in low or "user is deactivated" in low:
        return "bad_target"
    return "unknown"


# ---------------------------------------------------------------------------
# Discord — scoped_user_id 是 "{channel_id}_{user_id}", 取前半
# ---------------------------------------------------------------------------

def _discord_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "OpenProgram (https://github.com/Fzkuji/OpenProgram, 0.1)",
    }


def _post_discord(account_id: str, scoped_user_id: str, text: str) -> SendResult:
    creds = _accounts.load_credentials("discord", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return SendResult.fail("bad_target", f"malformed user id {scoped_user_id!r}")
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=_discord_headers(token),
            json={"content": text},
            timeout=10,
        )
        if not r.ok:
            return _classify_http_status(r.status_code, r.text)
        data = r.json()
        mid = data.get("id")
        return SendResult.success(str(mid) if mid else "")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _patch_discord(
    account_id: str, scoped_user_id: str, message_id: str, text: str,
) -> SendResult:
    creds = _accounts.load_credentials("discord", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return SendResult.fail("bad_target", f"malformed user id {scoped_user_id!r}")
    try:
        r = requests.patch(
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
            headers=_discord_headers(token),
            json={"content": text},
            timeout=10,
        )
        if not r.ok:
            return _classify_http_status(r.status_code, r.text)
        return SendResult.success(message_id)
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


# ---------------------------------------------------------------------------
# Slack — scoped_user_id 同 Discord. message_id 是 ts 字段.
# ---------------------------------------------------------------------------

def _slack_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _post_slack(account_id: str, scoped_user_id: str, text: str) -> SendResult:
    creds = _accounts.load_credentials("slack", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return SendResult.fail("bad_target", f"malformed user id {scoped_user_id!r}")
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
            kind = _slack_kind_from_error(err)
            return SendResult.fail(kind, err, retryable=(kind in ("rate_limit", "network")))
        ts = data.get("ts")
        return SendResult.success(str(ts) if ts else "")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _patch_slack(
    account_id: str, scoped_user_id: str, ts: str, text: str,
) -> SendResult:
    creds = _accounts.load_credentials("slack", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return SendResult.fail("bad_target", f"malformed user id {scoped_user_id!r}")
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
            kind = _slack_kind_from_error(err)
            return SendResult.fail(kind, err, retryable=(kind in ("rate_limit", "network")))
        return SendResult.success(ts)
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _slack_kind_from_error(err: str) -> str:
    """Slack API 错误代码 → error_kind. 见 Slack docs"errors" 部分."""
    low = (err or "").lower()
    if low in ("invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired"):
        return "auth"
    if low in ("rate_limited", "ratelimited"):
        return "rate_limit"
    if low in ("channel_not_found", "not_in_channel", "is_archived", "user_not_found"):
        return "bad_target"
    return "unknown"


# ---------------------------------------------------------------------------
# WeChat — iLink. Edit 不支持.
# ---------------------------------------------------------------------------

def _make_wechat_uin() -> str:
    """Stable-per-process X-WECHAT-UIN the iLink server expects."""
    uin = random.getrandbits(32)
    decimal = str(uin)
    return base64.b64encode(decimal.encode("ascii")).decode("ascii")


def _post_wechat(account_id: str, user_id: str, text: str) -> SendResult:
    creds = _accounts.load_credentials("wechat", account_id)
    bot_token = creds.get("bot_token") or ""
    bot_id = creds.get("ilink_bot_id") or ""
    base = creds.get("baseurl") or "https://ilinkai.weixin.qq.com"
    if not bot_token or not bot_id:
        return SendResult.fail("auth", f"account {account_id} not logged in")
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
        if not r.ok:
            return _classify_http_status(r.status_code, r.text)
        data = r.json() if r.ok else {}
        ret = data.get("ret", 0)
        if ret != 0:
            errmsg = data.get("errmsg", "?") or "?"
            kind = "auth" if ret in (401, 403, 1001) else "unknown"
            return SendResult.fail(kind, f"iLink ret={ret}: {errmsg[:200]}")
        # iLink 不返回稳定的 message_id, send_text 拿到的 handle 在 wechat
        # 上 editable=False (空 message_id). 这跟 wechat 不支持 edit 一致.
        return SendResult.success("")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


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
    # wechat: 不支持 edit, 缺这一项 → patch_message 返回 not_supported
}
