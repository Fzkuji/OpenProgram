"""Outbound — 入口 A: 无状态、一次性把文本送到 (channel, account, user).

这是 agentic-programming 范式 + cron 脚本 + jupyter 实验等场景用的发送入口::

    from openprogram.channels.outbound import send
    send("telegram", "default", "1234", "你好")

一行调用直接发出, 不需要任何 worker 进程在跑, 不持有 adapter 实例,
也不返回 message_id (用完即走的语义).

实际 HTTP 调用 / 凭据加载 / chunking / 错误处理在 :mod:`._transport`
里统一实现, 入口 B (``Channel.send_text`` / ``Channel.edit_text``)
走的是同一份底层.

两个公共函数:

* :func:`send` — 旧 API, 返回 bool. 给"我只想知道发没发出去"的简单
  调用方用 (e.g. webui/_execute/chat.py).
* :func:`send_full` — 新 API, 返回完整 :class:`SendResult` 含
  ``error_kind`` / ``retryable``. 给"我要在 UI 上告诉用户为什么发
  失败"的调用方用.

设计背景见 ``docs/design/channel-audit.md`` 第 5.F 节.
"""
from __future__ import annotations

from openprogram.channels import _transport
from openprogram.channels._transport import SendResult


def send(channel: str, account_id: str, user_id: str, text: str) -> bool:
    """Deliver ``text`` to (channel, account_id, user_id). Returns True
    on success.

    保留 bool 签名给 webui/_execute/chat.py 这类只关心成功/失败的
    caller. 想拿结构化失败原因用 :func:`send_full`.
    """
    if not text:
        return True
    return _transport.post_message(channel, account_id, user_id, text).ok


def send_full(
    channel: str, account_id: str, user_id: str, text: str,
) -> SendResult:
    """跟 :func:`send` 一样但返回完整 :class:`SendResult`.

    失败时 ``result.error_kind`` 标识类别 (``auth`` / ``rate_limit`` /
    ``bad_target`` / ``network`` / ``not_supported`` / ``unknown``),
    ``result.retryable`` 提示是否值得重试. UI 可以据此显示具体错误
    (e.g. "Telegram bot token 已失效, 请重新登录" 而非 "发送失败").
    """
    if not text:
        return SendResult.fail("bad_target", "empty text")
    return _transport.post_message(channel, account_id, user_id, text)
