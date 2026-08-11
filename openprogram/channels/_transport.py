"""共享底层 — 把消息字节送到 platform server, 一份实现给两个入口共用.

入口 A (``outbound.send``): 无状态、一次性、不需要 worker 进程在跑.
  agentic-programming 范式 + cron 脚本 + jupyter 实验用这条.

入口 B (``Channel.send_text`` / ``Channel.edit_text``): 长期挂着的
  adapter 实例, 保留 message_id 可以后续 edit. dispatcher 流式回复
  + progress streaming 用这条.

这两条入口走的"如何用 HTTP 把字节送到 platform" 是同一份代码 —— 都
调到 :func:`post_message` / :func:`patch_message` / :func:`post_file`.

出站流水线 (单点, 所有平台一致):

  1. chunk    — 按 :data:`MAX_CHARS` 切原始 markdown
  2. render   — :mod:`._format` 渲染成平台格式 (telegram HTML /
                slack mrkdwn / discord 透传 / wechat 纯文本); 渲染后
                超过 :data:`HARD_CAPS` 的 chunk 递归对半再切
  3. retry    — 遇 rate_limit (429 / flood) 退避重试, 优先读平台给的
                Retry-After, 最多 3 次尝试, 最终失败落日志

``error_kind`` 枚举:

  ``auth``         — 凭据问题: token 错、过期、bot 被踢
  ``rate_limit``   — 速率限制 (Telegram 429 / Discord 429 / Slack ratelimited)
  ``bad_target``   — 收信人不对: chat_id 错、channel 不存在、bot 没权限
  ``network``      — 连不上 / 超时 / SSL 错
  ``not_supported``— 平台不支持该操作 (WeChat edit / WeChat 发文件)
  ``format``       — 平台拒绝渲染后的格式 (telegram HTML 解析失败;
                     poster 内部已剥标签重发过, 走到外面说明兜底也失败)
  ``unknown``      — 其他, 看 error_detail
"""
from __future__ import annotations

import base64
import html as _html
import json
import mimetypes
import random
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from openprogram.channels import _format
from openprogram.channels import accounts as _accounts
from openprogram.security.safe_http import safe_client


# Platform-specific message size caps (原始 markdown 字符数, 渲染前).
# 单点定义 — chunking 只在 post_message 做, 所有出站路径共用.
MAX_CHARS: dict[str, int] = {
    "telegram": 4000,    # Bot API 硬上限 4096
    "slack":    39000,   # chat.postMessage 超 40000 字符截断 (文档同时
                         # 建议 4000 内显示最佳 — 我们取硬上限防丢内容)
    "discord":  1800,    # 硬上限 2000
    "wechat":   1800,    # iLink 无公开数字, 沿用保守值
}

# 渲染后的平台硬上限 — HTML escape / 标签会让 telegram 的渲染结果超出
# 原始长度, 超限的 chunk 在 _render_pieces 里递归对半再切.
HARD_CAPS: dict[str, int] = {
    "telegram": 4096,
    "discord":  2000,
    "slack":    40000,
}

# rate_limit 重试: 总共尝试 _RETRY_ATTEMPTS 次; 平台没给 Retry-After
# 时用 _RETRY_FALLBACK_DELAYS, 给了则用平台值 (封顶 _RETRY_SLEEP_CAP,
# 防止 progress-edit 路径被一个巨大 Retry-After 挂死).
_RETRY_ATTEMPTS = 3
_RETRY_FALLBACK_DELAYS = (1.0, 3.0)
_RETRY_SLEEP_CAP = 30.0


@dataclass(frozen=True)
class SendResult:
    """Send / edit 操作的结构化结果.

    ``ok`` True 时 ``message_id`` 是 platform-native 字符串 (可能为空,
    比如 WeChat 不返回可用 id 但发送成功). ``ok`` False 时 ``error_kind``
    标识失败类别, ``error_detail`` 是 human-readable 详情 (一行).
    ``retryable`` True 表示瞬态失败值得重试 (网络 / rate_limit).
    ``retry_after`` 是平台明示的等待秒数 (Retry-After header / telegram
    ``parameters.retry_after`` / discord 429 body), 0 = 平台没说.
    """
    ok: bool
    message_id: str = ""
    error_kind: str = ""
    error_detail: str = ""
    retryable: bool = False
    retry_after: float = 0.0

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls, message_id: str = "") -> "SendResult":
        return cls(ok=True, message_id=message_id)

    @classmethod
    def fail(
        cls, kind: str, detail: str = "", *,
        retryable: bool = False, retry_after: float = 0.0,
    ) -> "SendResult":
        return cls(ok=False, error_kind=kind, error_detail=detail,
                   retryable=retryable, retry_after=retry_after)


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def post_message(
    platform: str,
    account_id: str,
    target: str,
    text: str,
) -> SendResult:
    """发一条消息 (markdown 输入). 返回 :class:`SendResult`.

    ``target`` 字符串语义按 platform 不同 (见模块 docstring).

    长文本自动按平台 chunk 上限切分, 每段经 :mod:`._format` 渲染成平台
    格式后顺序发送; 返回 **最后一条** 的 SendResult — 中途失败立即返回
    当时的失败结果, 之前发出去的不撤回. rate_limit 失败在每段内部退避
    重试 (见模块 docstring), 重试耗尽才算失败.
    """
    if not text:
        return SendResult.fail("bad_target", "empty text")
    sender = _POSTERS.get(platform)
    if sender is None:
        return SendResult.fail(
            "not_supported", f"unknown platform {platform!r}",
        )
    limit = MAX_CHARS.get(platform, 1800)
    last: SendResult = SendResult.fail("unknown", "no chunks sent")
    for chunk in _chunk(text, limit):
        for piece in _render_pieces(platform, chunk):
            last = _send_with_retry(
                f"{platform}:{account_id}",
                lambda p=piece: sender(account_id, target, p),
            )
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
    """改一条已发出去的消息 (markdown 输入). 返回 :class:`SendResult`.

    WeChat 永远返回 ``not_supported`` error (iLink API 没有 editMessage).
    调用方应该用 ``post_message`` 发新消息代替. 渲染后超平台硬上限时
    降级成截断的纯文本 — 一条消息没法二分成两条 edit.
    """
    if not text:
        return SendResult.fail("bad_target", "empty text")
    patcher = _PATCHERS.get(platform)
    if patcher is None:
        return SendResult.fail(
            "not_supported",
            f"{platform!r} does not support editing messages",
        )
    rendered = _format.render(platform, text)
    cap = HARD_CAPS.get(platform)
    if cap is not None and len(rendered) > cap:
        rendered = _format.strip_markdown(text)
        if platform == "telegram":
            rendered = _html.escape(rendered, quote=False)
        rendered = rendered[:cap]
    return _send_with_retry(
        f"{platform}:{account_id}",
        lambda: patcher(account_id, target, message_id, rendered),
    )


def post_file(
    platform: str,
    account_id: str,
    target: str,
    path: str,
    caption: str = "",
) -> SendResult:
    """把本地文件发给 ``target``. 返回 :class:`SendResult`.

    平台能力: telegram (sendPhoto / sendDocument), discord (multipart
    upload), slack (files external-upload 三步). WeChat iLink 没有
    文件上传接口 → ``not_supported``, 调用方自行降级 (比如改发一条
    带路径说明的文本).
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return SendResult.fail("bad_target", f"no such file: {path}")
    sender = _FILE_POSTERS.get(platform)
    if sender is None:
        return SendResult.fail(
            "not_supported", f"{platform!r} cannot send files",
        )
    return _send_with_retry(
        f"{platform}:{account_id}",
        lambda: sender(account_id, target, p, caption),
    )


def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _render_pieces(platform: str, chunk: str) -> list[str]:
    """渲染一个原始 chunk; 渲染结果超平台硬上限时把原始 chunk 对半
    递归再切 (escape 是逐字符的, 减半原文约等于减半渲染结果)."""
    rendered = _format.render(platform, chunk)
    cap = HARD_CAPS.get(platform)
    if cap is None or len(rendered) <= cap or len(chunk) <= 1:
        return [rendered]
    mid = len(chunk) // 2
    return (_render_pieces(platform, chunk[:mid])
            + _render_pieces(platform, chunk[mid:]))


def _send_with_retry(tag: str, op: Callable[[], SendResult]) -> SendResult:
    """rate_limit 退避重试单点. 其他失败 (auth / bad_target / network /
    format) 直接返回 — network 超时重发有重复送达风险, 不自动重试."""
    result = SendResult.fail("unknown", "not attempted")
    for attempt in range(_RETRY_ATTEMPTS):
        result = op()
        if result.ok or result.error_kind != "rate_limit":
            return result
        if attempt == _RETRY_ATTEMPTS - 1:
            break
        wait = result.retry_after if result.retry_after > 0 else \
            _RETRY_FALLBACK_DELAYS[min(attempt, len(_RETRY_FALLBACK_DELAYS) - 1)]
        wait = min(wait, _RETRY_SLEEP_CAP)
        print(f"[{tag}] rate limited; retry "
              f"{attempt + 1}/{_RETRY_ATTEMPTS - 1} in {wait:.1f}s")
        time.sleep(wait)
    print(f"[{tag}] send failed after {_RETRY_ATTEMPTS} attempts: "
          f"{result.error_kind}: {result.error_detail}")
    return result


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


def _classify_http_status(
    status: int, body: str, retry_after_header: str = "",
) -> SendResult:
    """根据 HTTP status code + response body 给个 error_kind."""
    snippet = body[:200] if body else ""
    if status == 401 or status == 403:
        return SendResult.fail("auth", f"HTTP {status}: {snippet}")
    if status == 404:
        return SendResult.fail("bad_target", f"HTTP {status}: {snippet}")
    if status == 429:
        return SendResult.fail(
            "rate_limit", f"HTTP {status}: {snippet}", retryable=True,
            retry_after=_extract_retry_after(body, retry_after_header),
        )
    if 500 <= status < 600:
        return SendResult.fail("network", f"HTTP {status}: {snippet}", retryable=True)
    return SendResult.fail("unknown", f"HTTP {status}: {snippet}")


def _extract_retry_after(body: str, header: str = "") -> float:
    """Retry-After 秒数: header 优先, 其次 JSON body 里各平台的字段
    (telegram ``parameters.retry_after``, discord 顶层 ``retry_after``)."""
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return 0.0
    if isinstance(data, dict):
        val = data.get("retry_after")
        if val is None:
            val = (data.get("parameters") or {}).get("retry_after")
        try:
            return max(0.0, float(val))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Telegram — target 允许带 per-user 会话后缀 "{chat_id}_{user_id}", 取前半
# ---------------------------------------------------------------------------

_TG_PARSE_ERROR = re.compile(r"can't parse entities", re.I)


def _tg_chat_id(target: str) -> object:
    chat_id = target.partition("_")[0]
    return int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id


def _strip_html_tags(rendered: str) -> str:
    """telegram HTML 解析失败的兜底: 剥标签 + 反转义, 重新 escape 后
    仍然是合法 (纯文本) HTML."""
    plain = _html.unescape(re.sub(r"<[^>]+>", "", rendered))
    return _html.escape(plain, quote=False)


def _post_telegram(account_id: str, chat_id: str, text: str) -> SendResult:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    result = _tg_call(token, "sendMessage", {
        "chat_id": _tg_chat_id(chat_id), "text": text, "parse_mode": "HTML",
    })
    if not result.ok and result.error_kind == "format":
        result = _tg_call(token, "sendMessage", {
            "chat_id": _tg_chat_id(chat_id),
            "text": _strip_html_tags(text), "parse_mode": "HTML",
        })
    return result


def _patch_telegram(
    account_id: str, chat_id: str, message_id: str, text: str,
) -> SendResult:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    msg_id_val: object = int(message_id) if message_id.isdigit() else message_id
    payload = {
        "chat_id": _tg_chat_id(chat_id), "message_id": msg_id_val,
        "text": text, "parse_mode": "HTML",
    }
    result = _tg_call(token, "editMessageText", payload,
                      success_id=message_id)
    if not result.ok and result.error_kind == "format":
        payload["text"] = _strip_html_tags(text)
        result = _tg_call(token, "editMessageText", payload,
                          success_id=message_id)
    return result


def _tg_call(token: str, method: str, payload: dict,
             success_id: Optional[str] = None) -> SendResult:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload, timeout=10,
        )
        data = {}
        try:
            data = r.json()
        except ValueError:
            pass
        if not data.get("ok"):
            desc = data.get("description", "") or ""
            if desc:
                # editMessageText 在文本没变时回 "message is not
                # modified", 视为成功.
                if "not modified" in desc.lower():
                    return SendResult.success(success_id or "")
                kind = _telegram_kind_from_description(desc)
                return SendResult.fail(
                    kind, desc, retryable=(kind == "rate_limit"),
                    retry_after=_extract_retry_after(r.text),
                )
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        if success_id is not None:
            return SendResult.success(success_id)
        mid = (data.get("result") or {}).get("message_id")
        return SendResult.success(str(mid) if mid is not None else "")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _telegram_kind_from_description(desc: str) -> str:
    """从 Telegram 业务错误描述里推断 error_kind."""
    low = desc.lower()
    if _TG_PARSE_ERROR.search(low):
        return "format"
    if "unauthorized" in low or "bot token" in low:
        return "auth"
    if "too many requests" in low or "flood" in low:
        return "rate_limit"
    if "chat not found" in low or "bot was kicked" in low or "user is deactivated" in low:
        return "bad_target"
    return "unknown"


def _post_file_telegram(
    account_id: str, chat_id: str, path: Path, caption: str,
) -> SendResult:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    mime = mimetypes.guess_type(path.name)[0] or ""
    method, field = (
        ("sendPhoto", "photo") if mime.startswith("image/")
        else ("sendDocument", "document")
    )
    try:
        with path.open("rb") as fh:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/{method}",
                data={"chat_id": _tg_chat_id(chat_id),
                      "caption": caption[:1024]},
                files={field: (path.name, fh)},
                timeout=120,
            )
        data = {}
        try:
            data = r.json()
        except ValueError:
            pass
        if not data.get("ok"):
            desc = data.get("description", "") or ""
            if desc:
                kind = _telegram_kind_from_description(desc)
                return SendResult.fail(
                    kind, desc, retryable=(kind == "rate_limit"),
                    retry_after=_extract_retry_after(r.text),
                )
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        mid = (data.get("result") or {}).get("message_id")
        return SendResult.success(str(mid) if mid is not None else "")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


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
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
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
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        return SendResult.success(message_id)
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _post_file_discord(
    account_id: str, scoped_user_id: str, path: Path, caption: str,
) -> SendResult:
    creds = _accounts.load_credentials("discord", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return SendResult.fail("bad_target", f"malformed user id {scoped_user_id!r}")
    try:
        with path.open("rb") as fh:
            r = requests.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                # multipart: requests 自己定 Content-Type, 不能带 json 头
                headers={
                    "Authorization": f"Bot {token}",
                    "User-Agent": _discord_headers(token)["User-Agent"],
                },
                data={"payload_json": json.dumps({"content": caption})},
                files={"files[0]": (path.name, fh)},
                timeout=120,
            )
        if not r.ok:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        mid = r.json().get("id")
        return SendResult.success(str(mid) if mid else "")
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
        if not r.ok:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
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
        if not r.ok:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
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


def _post_file_slack(
    account_id: str, scoped_user_id: str, path: Path, caption: str,
) -> SendResult:
    """Slack external-upload 三步: getUploadURLExternal → PUT 字节 →
    completeUploadExternal (旧 files.upload 已停用)."""
    creds = _accounts.load_credentials("slack", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return SendResult.fail("bad_target", f"malformed user id {scoped_user_id!r}")
    try:
        size = path.stat().st_size
        r = requests.post(
            "https://slack.com/api/files.getUploadURLExternal",
            headers={"Authorization": f"Bearer {token}"},
            data={"filename": path.name, "length": str(size)},
            timeout=10,
        )
        if not r.ok:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
        if not data.get("ok"):
            err = data.get("error") or r.text[:200]
            kind = _slack_kind_from_error(err)
            return SendResult.fail(kind, err, retryable=(kind == "rate_limit"))
        upload_url = data.get("upload_url")
        file_id = data.get("file_id")
        if not upload_url or not file_id:
            return SendResult.fail("unknown", "upload URL response missing fields")

        with path.open("rb") as fh, safe_client(
            "channel.slack.generated_asset.upload"
        ) as client:
            up = client.post(upload_url, content=fh, timeout=120)
        if not up.is_success:
            return _classify_http_status(up.status_code, up.text)

        r = requests.post(
            "https://slack.com/api/files.completeUploadExternal",
            headers=_slack_headers(token),
            json={
                "files": [{"id": file_id, "title": path.name}],
                "channel_id": channel_id,
                **({"initial_comment": caption} if caption else {}),
            },
            timeout=30,
        )
        if not r.ok:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
        if not data.get("ok"):
            err = data.get("error") or r.text[:200]
            kind = _slack_kind_from_error(err)
            return SendResult.fail(kind, err, retryable=(kind == "rate_limit"))
        return SendResult.success(str(file_id))
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


# ---------------------------------------------------------------------------
# WeChat — iLink. Edit / 文件上传不支持.
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
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
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

_FILE_POSTERS = {
    "telegram": _post_file_telegram,
    "discord":  _post_file_discord,
    "slack":    _post_file_slack,
    # wechat: iLink 无文件上传接口, 缺这一项 → post_file 返回 not_supported
}
