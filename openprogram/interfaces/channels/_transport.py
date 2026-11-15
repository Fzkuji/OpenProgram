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
  ``not_supported``— 平台不支持该操作 (WeChat edit)
  ``format``       — 平台拒绝渲染后的格式 (telegram HTML 解析失败;
                     poster 内部已剥标签重发过, 走到外面说明兜底也失败)
  ``unknown``      — 其他, 看 error_detail
"""
from __future__ import annotations

import base64
import html as _html
import json
import logging
import mimetypes
import random
import re
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

import httpx

from openprogram.interfaces.channels import _format
from openprogram.interfaces.channels import accounts as _accounts
from openprogram.host.security.safe_http import configured_safe_client, safe_client
from openprogram.host.security.url_policy import (
    OwnerURLException,
    URLPolicyError,
    normalize_origin,
)

_log = logging.getLogger(__name__)


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

# 连接没建立的重试: 请求没发出去, 重发不会重复送达. 代理或网络抖动时
# 单次连接常失败, 而一条回复丢了就找不回来.
_CONNECT_RETRY_ATTEMPTS = 4
_CONNECT_RETRY_DELAYS = (1.0, 2.0, 4.0)

# 切开代码块时给收尾 fence "\n```" 留的余量.
_FENCE_CLOSE_ROOM = 8
# 代码块开头行 (``` + 语言名) 的合理长度上限.
_MAX_FENCE_OPENER = 64


@dataclass(frozen=True)
class SendResult:
    """Send / edit 操作的结构化结果.

    ``ok`` True 时 ``message_id`` 是 platform-native 字符串 (可能为空,
    比如 WeChat 不返回可用 id 但发送成功). ``ok`` False 时 ``error_kind``
    标识失败类别, ``error_detail`` 是 human-readable 详情 (一行).
    ``retryable`` True 表示瞬态失败值得重试 (网络 / rate_limit).
    ``retry_after`` 是平台明示的等待秒数 (Retry-After header / telegram
    ``parameters.retry_after`` / discord 429 body), 0 = 平台没说.
    ``connect_failed`` True 表示连接没建立且前面没有已发出的片段, 平台
    一点也没收到, 重发不会重复送达.
    """
    ok: bool
    message_id: str = ""
    error_kind: str = ""
    error_detail: str = ""
    retryable: bool = False
    # No connection was made and no earlier piece went out, so the platform
    # has none of the message.
    connect_failed: bool = False
    retry_after: float = 0.0

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls, message_id: str = "") -> "SendResult":
        return cls(ok=True, message_id=message_id)

    @classmethod
    def fail(
        cls, kind: str, detail: str = "", *,
        retryable: bool = False, retry_after: float = 0.0, connect_failed: bool = False,
    ) -> "SendResult":
        return cls(ok=False, error_kind=kind, error_detail=detail,
                   retryable=retryable, retry_after=retry_after, connect_failed=connect_failed)


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def post_message(
    platform: str,
    account_id: str,
    target: str,
    text: str,
    thread_id: Optional[str] = None,
) -> SendResult:
    """发一条消息 (markdown 输入). 返回 :class:`SendResult`.

    ``target`` 字符串语义按 platform 不同 (见模块 docstring).
    ``thread_id`` 把消息发进一个 thread: Slack ``thread_ts`` / Telegram
    论坛话题 ``message_thread_id``; 其他平台忽略. None = 发到会话根部.

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
    thread_kw = _thread_kwargs(platform, thread_id)
    last: SendResult = SendResult.fail("unknown", "no chunks sent")
    delivered_a_piece = False
    # 先摘掉再切: 切开的 visual 块每段都会各渲染出一句提示.
    for chunk in _chunk(_format.drop_visuals(text), limit):
        for piece in _render_pieces(platform, chunk):
            last = _send_with_retry(
                f"{platform}:{account_id}",
                lambda p=piece: sender(account_id, target, p, **thread_kw),
            )
            if not last.ok:
                # The platform has the earlier pieces, so sending the whole
                # text again would repeat them.
                return replace(last, connect_failed=False) if delivered_a_piece else last
            delivered_a_piece = True
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
    thread_id: Optional[str] = None,
) -> SendResult:
    """把本地文件发给 ``target``. 返回 :class:`SendResult`.

    ``thread_id`` 同 :func:`post_message`.

    平台能力: telegram (sendPhoto / sendDocument), discord (multipart
    upload), slack (files external-upload 三步), wechat (iLink CDN 加密
    上传, 图片 / 视频 / 文件三种条目). 不在表里的平台 → ``not_supported``,
    调用方自行降级 (比如改发一条带路径说明的文本).
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return SendResult.fail("bad_target", f"no such file: {path}")
    sender = _FILE_POSTERS.get(platform)
    if sender is None:
        return SendResult.fail(
            "not_supported", f"{platform!r} cannot send files",
        )
    thread_kw = _thread_kwargs(platform, thread_id)
    return _send_with_retry(
        f"{platform}:{account_id}",
        lambda: sender(account_id, target, p, caption, **thread_kw),
    )


#: 出站能发进 thread 的平台. 其他平台的 thread 要么本身就是独立
#: channel (Discord), 要么没有 thread (WeChat).
_THREADED_PLATFORMS = frozenset({"telegram", "slack"})


def _thread_kwargs(platform: str, thread_id: Optional[str]) -> dict:
    if thread_id and platform in _THREADED_PLATFORMS:
        return {"thread_id": str(thread_id)}
    return {}


def split_for_edit(platform: str, text: str, marker: str = "") -> tuple[str, str]:
    """把一条回复切成 ``(head, tail)``: head 渲染后放得进一次 edit
    (:data:`HARD_CAPS` / :data:`MAX_CHARS`), tail 留给 :func:`post_message`.

    放得下 (一个原始 chunk 且渲染后只有一段) 时返回 ``(text, "")``.
    否则在行边界切; 切点落在代码块里时 head 补上收尾 fence, tail 用
    同一行开头 fence 重新打开. ``marker`` (e.g. "continued") 拼在 head
    末尾, 长度一并计入.
    """
    limit = MAX_CHARS.get(platform, 1800)
    text = _format.drop_visuals(text)
    if len(text) <= limit and len(_render_pieces(platform, text)) == 1:
        return text, ""
    cap = HARD_CAPS.get(platform)
    budget = limit - len(marker) - _FENCE_CLOSE_ROOM
    while budget > 0:
        head, tail = _cut_at_line(text, budget)
        candidate = head + marker
        if cap is None or len(_format.render(platform, candidate)) <= cap:
            return candidate, tail
        budget = int(budget * 0.8)
    return text[:1] + marker, text[1:]


def _cut_at_line(text: str, budget: int) -> tuple[str, str]:
    """在 ``budget`` 字以内的最后一个换行处切开; 切点落在代码块里时 head
    补上收尾 fence, tail 用同一行开头 fence 重新打开.

    代码块的开头行后面还没有内容时不在这里切: 否则 head 是空代码块,
    tail 又从同一个开头行开始, 永远切不完. 切点正好在收尾 fence 前时
    收尾 fence 归 head, tail 不重开 — 否则下一条是个空代码块, Telegram
    会拒收. head 为空表示这一段只有空行, 没有要发的内容."""
    newline = text.rfind("\n", 0, budget)
    cut = newline + 1 if newline > 0 else _hard_cut(text, budget)
    head = text[:cut].rstrip("\n")
    if head.count("```") % 2 == 1 and "\n" not in head[head.rfind("```"):]:
        opening = head.rfind("```")
        if head[:opening].strip():
            return head[:opening].rstrip("\n"), text[opening:]
        cut = _hard_cut(text, budget)
    head, tail = text[:cut].rstrip("\n"), text[cut:].lstrip("\n")
    if head.count("```") % 2 == 1:
        block = head[head.rfind("```"):]
        opener = block.split("\n", 1)[0]
        # 超长的"开头行"不是真的代码块; 重复它 tail 只会越切越长.
        if len(opener) <= _MAX_FENCE_OPENER:
            head = head + "\n```" if "\n" in block else ""
            if tail.startswith("```"):
                tail = tail[3:].lstrip("\n")
            else:
                tail = opener + "\n" + tail
    return head, tail


def _hard_cut(text: str, budget: int) -> int:
    """一行放不下时的切点, 不把 ``` 记号切成两半."""
    cut = budget
    for _ in range(2):
        if 1 < cut < len(text) and text[cut - 1] == "`" and text[cut] == "`":
            cut -= 1
    return cut


def _chunk(text: str, limit: int) -> list[str]:
    """按 ``limit`` 切原始 markdown, 每段的代码块都完整闭合."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        head, rest = _cut_at_line(rest, limit - _FENCE_CLOSE_ROOM)
        if head.strip():
            chunks.append(head)
    if rest.strip() or not chunks:
        chunks.append(rest)
    return chunks


def _render_pieces(platform: str, chunk: str) -> list[str]:
    """渲染一个原始 chunk; 渲染结果超平台硬上限时在一半附近再切, 递归
    直到放得下 (escape 是逐字符的, 减半原文约等于减半渲染结果)."""
    rendered = _format.render(platform, chunk)
    cap = HARD_CAPS.get(platform)
    if cap is None or len(rendered) <= cap or len(chunk) <= 1:
        return [rendered]
    mid = len(chunk) // 2
    head, tail = _cut_at_line(chunk, mid)
    if not head or not tail or len(head) >= len(chunk) or len(tail) >= len(chunk):
        head, tail = chunk[:mid], chunk[mid:]
    return _render_pieces(platform, head) + _render_pieces(platform, tail)


def _send_with_retry(tag: str, op: Callable[[], SendResult]) -> SendResult:
    """退避重试单点: rate_limit 和连接没建立 (``connect_failed``). 其他失败
    (auth / bad_target / format / 请求发出后的 network) 直接返回 —
    请求发出后超时再重发有重复送达风险, 不自动重试."""
    rate_limited = unconnected = 0
    while True:
        result = op()
        if result.ok:
            return result
        if result.connect_failed:
            unconnected += 1
            if unconnected >= _CONNECT_RETRY_ATTEMPTS:
                break
            wait = _CONNECT_RETRY_DELAYS[unconnected - 1]
        elif result.error_kind == "rate_limit":
            rate_limited += 1
            if rate_limited >= _RETRY_ATTEMPTS:
                break
            wait = result.retry_after if result.retry_after > 0 else \
                _RETRY_FALLBACK_DELAYS[min(rate_limited - 1, len(_RETRY_FALLBACK_DELAYS) - 1)]
            wait = min(wait, _RETRY_SLEEP_CAP)
        else:
            return result
        _log.info("[%s] %s; retry in %.1fs", tag, result.error_kind, wait)
        time.sleep(wait)
    _log.error("[%s] send failed after retries: %s: %s",
               tag, result.error_kind, result.error_detail)
    return result


# ---------------------------------------------------------------------------
# 错误分类 helpers
# ---------------------------------------------------------------------------

def _classify_network_error(exc: Exception) -> SendResult:
    """request 库异常 → SendResult."""
    detail = exc.reason if isinstance(exc, URLPolicyError) else type(exc).__name__
    # 所有 HTTP 异常都当 network, 给 retryable=True. 下面这几种失败时请求
    # 肯定没离开本机, 只有它们重发不会重复送达.
    connect_failed = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError))
    return SendResult.fail("network", detail, retryable=True, connect_failed=connect_failed)


def _classify_http_status(
    status: int, body: str, retry_after_header: str = "",
) -> SendResult:
    """根据 HTTP status code + response body 给个 error_kind."""
    if status == 401 or status == 403:
        return SendResult.fail("auth", f"HTTP {status}")
    if status == 404:
        return SendResult.fail("bad_target", f"HTTP {status}")
    if status == 429:
        return SendResult.fail(
            "rate_limit", f"HTTP {status}", retryable=True,
            retry_after=_extract_retry_after(body, retry_after_header),
        )
    if 500 <= status < 600:
        return SendResult.fail("network", f"HTTP {status}", retryable=True)
    return SendResult.fail("unknown", f"HTTP {status}")


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
_TG_ERROR_DETAIL_LIMIT = 300


def _tg_chat_id(target: str) -> object:
    chat_id = target.partition("_")[0]
    return int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id


def _strip_html_tags(rendered: str) -> str:
    """telegram HTML 解析失败的兜底: 剥标签 + 反转义, 重新 escape 后
    仍然是合法 (纯文本) HTML."""
    plain = _html.unescape(re.sub(r"<[^>]+>", "", rendered))
    return _html.escape(plain, quote=False)


def _tg_thread(thread_id: Optional[str]) -> dict:
    """论坛话题: ``message_thread_id`` 让回复落在原话题而不是 General."""
    if not thread_id:
        return {}
    return {"message_thread_id": int(thread_id) if thread_id.isdigit() else thread_id}


def _post_telegram(account_id: str, chat_id: str, text: str,
                   thread_id: Optional[str] = None) -> SendResult:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    result = _tg_call(token, "sendMessage", {
        "chat_id": _tg_chat_id(chat_id), "text": text, "parse_mode": "HTML",
        **_tg_thread(thread_id),
    })
    if not result.ok and result.error_kind == "format":
        result = _tg_call(token, "sendMessage", {
            "chat_id": _tg_chat_id(chat_id),
            "text": _strip_html_tags(text), "parse_mode": "HTML",
            **_tg_thread(thread_id),
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
        with safe_client("channel.telegram.api") as client:
            r = client.post(
                f"https://api.telegram.org/bot{token}/{method}",
                json=payload,
                timeout=10,
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
                return _tg_api_error(desc, r.text)
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        if success_id is not None:
            return SendResult.success(success_id)
        mid = (data.get("result") or {}).get("message_id")
        return SendResult.success(str(mid) if mid is not None else "")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _tg_api_error(desc: str, body: str) -> SendResult:
    """Telegram 业务错误 → SendResult, ``error_detail`` 保留平台给的原因."""
    kind = _telegram_kind_from_description(desc)
    return SendResult.fail(
        kind,
        " ".join(desc.split())[:_TG_ERROR_DETAIL_LIMIT],
        retryable=(kind == "rate_limit"),
        retry_after=_extract_retry_after(body),
    )


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
    thread_id: Optional[str] = None,
) -> SendResult:
    creds = _accounts.load_credentials("telegram", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    mime = mimetypes.guess_type(path.name)[0] or ""
    def upload(method: str, field: str) -> SendResult:
        return _tg_upload(token, method, field, chat_id, path, caption, thread_id)

    if not mime.startswith("image/"):
        return upload("sendDocument", "document")
    result = upload("sendPhoto", "photo")
    # Telegram 明确拒收的图片 (尺寸、比例、大小) 还能当文件发. 超时等
    # 结果不明的失败不重发, 否则同一张图可能送达两次.
    if not result.ok and result.error_kind in ("format", "unknown"):
        _log.info("[telegram:%s] photo refused (%s); sending as a document",
                  account_id, result.error_detail)
        return upload("sendDocument", "document")
    return result


def _tg_upload(
    token: str, method: str, field: str, chat_id: str, path: Path,
    caption: str, thread_id: Optional[str],
) -> SendResult:
    try:
        with path.open("rb") as fh:
            with safe_client("channel.telegram.api") as client:
                r = client.post(
                    f"https://api.telegram.org/bot{token}/{method}",
                    data={"chat_id": _tg_chat_id(chat_id), "caption": caption[:1024],
                          **_tg_thread(thread_id)},
                    files={field: (path.name, fh)},
                    timeout=120,
                )
        data = {}
        try:
            data = r.json()
        except ValueError:
            # 2xx 但读不出结果: 可能已经送达, 不能算作平台拒收.
            if r.is_success:
                return SendResult.fail(
                    "network", "unreadable Telegram response", retryable=True,
                )
        if not data.get("ok"):
            desc = data.get("description", "") or ""
            if desc:
                return _tg_api_error(desc, r.text)
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

# 回复里的文字来自模型和网页内容, 不能让它 ping @everyone / 角色 / 用户.
_DISCORD_NO_MENTIONS = {"parse": []}


def _discord_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "OpenProgram (https://github.com/FuzzyKuji/OpenProgram, 0.1)",
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
        with safe_client("channel.discord.api") as client:
            r = client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=_discord_headers(token),
                json={"content": text, "allowed_mentions": _DISCORD_NO_MENTIONS},
                timeout=10,
            )
        if not r.is_success:
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
        with safe_client("channel.discord.api") as client:
            r = client.patch(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
                headers=_discord_headers(token),
                json={"content": text, "allowed_mentions": _DISCORD_NO_MENTIONS},
                timeout=10,
            )
        if not r.is_success:
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
            with safe_client("channel.discord.api") as client:
                r = client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    # multipart: httpx 自己定 Content-Type, 不能带 json 头
                    headers={
                        "Authorization": f"Bot {token}",
                        "User-Agent": _discord_headers(token)["User-Agent"],
                    },
                    data={"payload_json": json.dumps({
                        "content": caption, "allowed_mentions": _DISCORD_NO_MENTIONS,
                    })},
                    files={"files[0]": (path.name, fh)},
                    timeout=120,
                )
        if not r.is_success:
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


def _post_slack(account_id: str, scoped_user_id: str, text: str,
                thread_id: Optional[str] = None) -> SendResult:
    creds = _accounts.load_credentials("slack", account_id)
    token = creds.get("bot_token")
    if not token:
        return SendResult.fail("auth", f"account {account_id} has no bot_token")
    channel_id, _, _user = scoped_user_id.partition("_")
    if not channel_id:
        return SendResult.fail("bad_target", f"malformed user id {scoped_user_id!r}")
    try:
        with safe_client("channel.slack.api") as client:
            r = client.post(
                "https://slack.com/api/chat.postMessage",
                headers=_slack_headers(token),
                json={"channel": channel_id, "text": text,
                      **({"thread_ts": thread_id} if thread_id else {})},
                timeout=10,
            )
        if not r.is_success:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
        if not data.get("ok"):
            err = str(data.get("error") or "unknown")
            kind = _slack_kind_from_error(err)
            return SendResult.fail(
                kind,
                "slack_api_error",
                retryable=(kind in ("rate_limit", "network")),
            )
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
        with safe_client("channel.slack.api") as client:
            r = client.post(
                "https://slack.com/api/chat.update",
                headers=_slack_headers(token),
                json={"channel": channel_id, "ts": ts, "text": text},
                timeout=10,
            )
        if not r.is_success:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
        if not data.get("ok"):
            err = str(data.get("error") or "unknown")
            kind = _slack_kind_from_error(err)
            return SendResult.fail(
                kind,
                "slack_api_error",
                retryable=(kind in ("rate_limit", "network")),
            )
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
    thread_id: Optional[str] = None,
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
        with safe_client("channel.slack.api") as client:
            r = client.post(
                "https://slack.com/api/files.getUploadURLExternal",
                headers={"Authorization": f"Bearer {token}"},
                data={"filename": path.name, "length": str(size)},
                timeout=10,
            )
        if not r.is_success:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
        if not data.get("ok"):
            err = str(data.get("error") or "unknown")
            kind = _slack_kind_from_error(err)
            return SendResult.fail(
                kind, "slack_api_error", retryable=(kind == "rate_limit")
            )
        upload_url = data.get("upload_url")
        file_id = data.get("file_id")
        if not upload_url or not file_id:
            return SendResult.fail("unknown", "upload URL response missing fields")

        try:
            with path.open("rb") as fh, safe_client(
                "channel.slack.generated_asset.upload"
            ) as client:
                up = client.post(upload_url, content=fh, timeout=120)
        except httpx.RequestError as e:
            return SendResult.fail(
                "network",
                f"{type(e).__name__} for {normalize_origin(upload_url)}",
                retryable=True,
            )
        if not up.is_success:
            return _classify_http_status(up.status_code, up.text)

        with safe_client("channel.slack.api") as client:
            r = client.post(
                "https://slack.com/api/files.completeUploadExternal",
                headers=_slack_headers(token),
                json={
                    "files": [{"id": file_id, "title": path.name}],
                    "channel_id": channel_id,
                    **({"initial_comment": caption} if caption else {}),
                    **({"thread_ts": thread_id} if thread_id else {}),
                },
                timeout=30,
            )
        if not r.is_success:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json()
        if not data.get("ok"):
            err = str(data.get("error") or "unknown")
            kind = _slack_kind_from_error(err)
            return SendResult.fail(
                kind, "slack_api_error", retryable=(kind == "rate_limit")
            )
        return SendResult.success(str(file_id))
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


# ---------------------------------------------------------------------------
# WeChat — iLink. 不支持 edit; 文件走 CDN 加密上传.
# ---------------------------------------------------------------------------

def _make_wechat_uin() -> str:
    """Stable-per-process X-WECHAT-UIN the iLink server expects."""
    uin = random.getrandbits(32)
    decimal = str(uin)
    return base64.b64encode(decimal.encode("ascii")).decode("ascii")


def _post_wechat(account_id: str, user_id: str, text: str) -> SendResult:
    return _send_wechat_item(
        account_id, user_id, {"type": 1, "text_item": {"text": text}},
    )


def _send_wechat_item(account_id: str, user_id: str, item: dict) -> SendResult:
    """``sendmessage`` 发一个条目 (文本或上传好的媒体)."""
    creds = _accounts.load_credentials("wechat", account_id)
    bot_token = creds.get("bot_token") or ""
    bot_id = creds.get("ilink_bot_id") or ""
    base = creds.get("baseurl") or "https://ilinkai.weixin.qq.com"
    if not bot_token or not bot_id:
        return SendResult.fail("auth", f"account {account_id} not logged in")
    try:
        configured_origin = normalize_origin(base)
        with configured_safe_client(
            "channel.wechat.api",
            configured_origin,
            owner_exception=OwnerURLException(
                consumer="channel.wechat.api", origin=configured_origin
            ),
        ) as client:
            r = client.post(
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
                        "item_list": [item],
                        "context_token": "",
                    },
                    "base_info": {},
                },
                timeout=15,
            )
        if not r.is_success:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        data = r.json() if r.is_success else {}
        ret = data.get("ret", 0)
        if ret != 0:
            kind = "auth" if ret in (401, 403, 1001) else "unknown"
            return SendResult.fail(kind, f"iLink ret={ret}")
        # iLink 不返回稳定的 message_id, send_text 拿到的 handle 在 wechat
        # 上 editable=False (空 message_id). 这跟 wechat 不支持 edit 一致.
        return SendResult.success("")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


#: 出站文件上限. iLink 插件的媒体上限是 100 MB; 整个文件要读进内存加密.
WECHAT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024


def _post_file_wechat(
    account_id: str, user_id: str, path: Path, caption: str,
) -> SendResult:
    """getuploadurl → AES-128-ECB 加密 → POST 到 CDN → sendmessage 发图片 /
    视频 / 文件条目. 说明文字在文件之后单独发一条 (iLink 条目没有 caption)."""
    import hashlib
    import os
    from urllib.parse import urlsplit

    from openprogram.interfaces.channels import _wechat_media as media

    size = path.stat().st_size
    if size > WECHAT_MAX_UPLOAD_BYTES:
        return SendResult.fail(
            "bad_target", f"{size} bytes exceeds {WECHAT_MAX_UPLOAD_BYTES}",
        )
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    upload_type, item_type = media.upload_kind(mime)
    try:
        plaintext = path.read_bytes()
        key = os.urandom(16)
        filekey = os.urandom(16).hex()
        ciphertext = media.encrypt(plaintext, key)
        data = _wechat_post(account_id, "getuploadurl", {
            "filekey": filekey,
            "media_type": upload_type,
            "to_user_id": user_id,
            "rawsize": len(plaintext),
            "rawfilemd5": hashlib.md5(plaintext).hexdigest(),  # noqa: S324 — iLink 协议字段
            "filesize": len(ciphertext),
            "no_need_thumb": True,
            "aeskey": key.hex(),
        })
        if data.get("ret", 0) != 0:
            return SendResult.fail("unknown", f"iLink getuploadurl ret={data.get('ret')}")
        param = str(data.get("upload_param") or "")
        full = str(data.get("upload_full_url") or "").strip()
        target = media.upload_url(param, filekey) if param else full
        if not target:
            return SendResult.fail("unknown", "getuploadurl returned no upload URL")
        try:
            with safe_client("channel.wechat.cdn") as client:
                up = client.post(
                    target, content=ciphertext,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=120,
                )
        except httpx.RequestError as e:
            return SendResult.fail(
                "network", f"{type(e).__name__} for {normalize_origin(target)}",
                retryable=True,
            )
        if up.status_code != 200:
            return _classify_http_status(up.status_code, up.headers.get("x-error-message", ""))
        download_param = up.headers.get("x-encrypted-param", "")
        if not download_param:
            return SendResult.fail(
                "unknown",
                f"{urlsplit(target).hostname}: upload response has no x-encrypted-param",
            )
        result = _send_wechat_item(account_id, user_id, media.media_item(
            item_type, download_param=download_param, key=key, name=path.name,
            raw_size=len(plaintext), cipher_size=len(ciphertext),
        ))
    except PermissionError as e:
        return SendResult.fail("auth", str(e))
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)
    if result.ok and caption.strip():
        _post_wechat(account_id, user_id, caption)
    return result


# ---------------------------------------------------------------------------
# "正在输入" 和确认表情 — 提示性操作, 不重试, 失败由调用方忽略
# ---------------------------------------------------------------------------

#: 各平台 typing 状态的刷新间隔 (秒): Telegram 约 5 秒失效, Discord 约
#: 10 秒, iLink 插件按 5 秒保活. Slack bot 没有 typing API, 不在表里.
TYPING_REFRESH_SECONDS: dict[str, float] = {
    "telegram": 4.0,
    "discord": 8.0,
    "wechat": 5.0,
}

ACK_EMOJI = "👀"

#: WeChat 每个用户最近一条入站消息的 context_token — iLink 取 typing
#: ticket (getconfig) 要带它. adapter 收消息时登记.
_WECHAT_CONTEXT: dict[tuple[str, str], str] = {}
#: (account, user) → (typing_ticket, 取到的时间). iLink 插件缓存 24h.
_WECHAT_TICKETS: dict[tuple[str, str], tuple[str, float]] = {}
_WECHAT_TICKET_TTL = 6 * 3600.0


def remember_wechat_context(account_id: str, user_id: str, token: str) -> None:
    if token:
        _WECHAT_CONTEXT[(account_id, user_id)] = token


def send_typing(platform: str, account_id: str, target: str,
                thread_id: Optional[str] = None) -> SendResult:
    """显示一次"正在输入"; 有效期见 :data:`TYPING_REFRESH_SECONDS`.
    ``thread_id`` 让 Telegram 论坛话题里显示在原话题; 其他平台用不上."""
    if platform == "telegram":
        token = _accounts.load_credentials("telegram", account_id).get("bot_token")
        if not token:
            return SendResult.fail("auth", "no bot_token")
        return _tg_call(token, "sendChatAction", {
            "chat_id": _tg_chat_id(target), "action": "typing",
            **_tg_thread(thread_id),
        }, success_id="")
    if platform == "discord":
        channel_id = target.partition("_")[0]
        return _discord_call(account_id, "POST",
                             f"/channels/{channel_id}/typing")
    if platform == "wechat":
        return _wechat_typing(account_id, target, status=1)
    return SendResult.fail("not_supported", f"{platform} has no typing indicator")


def stop_typing(platform: str, account_id: str, target: str) -> SendResult:
    """收起"正在输入". 只有 iLink 有显式取消, 其他平台到期自动消失."""
    if platform == "wechat":
        return _wechat_typing(account_id, target, status=2)
    return SendResult.success("")


def add_reaction(platform: str, account_id: str, chat_id: str,
                 message_id: str) -> SendResult:
    """给一条入站消息加 :data:`ACK_EMOJI`, 表示机器人已接手."""
    if platform == "telegram":
        token = _accounts.load_credentials("telegram", account_id).get("bot_token")
        if not token:
            return SendResult.fail("auth", "no bot_token")
        return _tg_call(token, "setMessageReaction", {
            "chat_id": _tg_chat_id(chat_id),
            "message_id": int(message_id) if message_id.isdigit() else message_id,
            "reaction": [{"type": "emoji", "emoji": ACK_EMOJI}],
        }, success_id=message_id)
    if platform == "discord":
        from urllib.parse import quote
        return _discord_call(
            account_id, "PUT",
            f"/channels/{chat_id}/messages/{message_id}/reactions/"
            f"{quote(ACK_EMOJI)}/@me",
        )
    if platform == "slack":
        token = _accounts.load_credentials("slack", account_id).get("bot_token")
        if not token:
            return SendResult.fail("auth", "no bot_token")
        try:
            with safe_client("channel.slack.api") as client:
                r = client.post(
                    "https://slack.com/api/reactions.add",
                    headers=_slack_headers(token),
                    json={"channel": chat_id, "timestamp": message_id,
                          "name": "eyes"},
                    timeout=10,
                )
            data = r.json() if r.is_success else {}
            if not data.get("ok"):
                return SendResult.fail(
                    _slack_kind_from_error(str(data.get("error") or "")),
                    "slack_api_error",
                )
            return SendResult.success(message_id)
        except Exception as e:  # noqa: BLE001
            return _classify_network_error(e)
    return SendResult.fail("not_supported", f"{platform} has no reactions")


def _discord_call(account_id: str, method: str, path: str) -> SendResult:
    token = _accounts.load_credentials("discord", account_id).get("bot_token")
    if not token:
        return SendResult.fail("auth", "no bot_token")
    try:
        with safe_client("channel.discord.api") as client:
            r = client.request(
                method, f"https://discord.com/api/v10{path}",
                headers=_discord_headers(token), timeout=10,
            )
        if not r.is_success:
            return _classify_http_status(
                r.status_code, r.text, r.headers.get("Retry-After", ""),
            )
        return SendResult.success("")
    except Exception as e:  # noqa: BLE001
        return _classify_network_error(e)


def _wechat_post(account_id: str, endpoint: str, body: dict) -> dict:
    creds = _accounts.load_credentials("wechat", account_id)
    bot_token = creds.get("bot_token") or ""
    base = creds.get("baseurl") or "https://ilinkai.weixin.qq.com"
    if not bot_token:
        raise PermissionError("wechat account not logged in")
    origin = normalize_origin(base)
    with configured_safe_client(
        "channel.wechat.api", origin,
        owner_exception=OwnerURLException(
            consumer="channel.wechat.api", origin=origin,
        ),
    ) as client:
        r = client.post(
            f"{base}/ilink/bot/{endpoint}",
            headers={
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "Authorization": f"Bearer {bot_token}",
                "X-WECHAT-UIN": _make_wechat_uin(),
            },
            json={**body, "base_info": {}},
            timeout=10,
        )
    r.raise_for_status()
    return r.json() if r.content else {}


def _wechat_typing(account_id: str, user_id: str, *, status: int) -> SendResult:
    key = (account_id, user_id)
    try:
        cached = _WECHAT_TICKETS.get(key)
        if cached is None or time.time() - cached[1] > _WECHAT_TICKET_TTL:
            data = _wechat_post(account_id, "getconfig", {
                "ilink_user_id": user_id,
                "context_token": _WECHAT_CONTEXT.get(key, ""),
            })
            ticket = str(data.get("typing_ticket") or "")
            if data.get("ret", 0) != 0 or not ticket:
                return SendResult.fail("not_supported", "no typing ticket")
            cached = _WECHAT_TICKETS[key] = (ticket, time.time())
        data = _wechat_post(account_id, "sendtyping", {
            "ilink_user_id": user_id,
            "typing_ticket": cached[0],
            "status": status,
        })
        if data.get("ret", 0) != 0:
            _WECHAT_TICKETS.pop(key, None)
            return SendResult.fail("unknown", f"iLink ret={data.get('ret')}")
        return SendResult.success("")
    except PermissionError as e:
        return SendResult.fail("auth", str(e))
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
    "wechat":   _post_file_wechat,
}
