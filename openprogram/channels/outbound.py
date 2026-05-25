"""Outbound — 入口 A: 无状态、一次性把文本送到 (channel, account, user).

这是 agentic-programming 范式 + cron 脚本 + jupyter 实验等场景用的发送入口::

    from openprogram.channels.outbound import send
    send("telegram", "default", "1234", "你好")

一行调用直接发出, 不需要任何 worker 进程在跑, 不持有 adapter 实例,
也不返回 message_id (用完即走的语义).

实际 HTTP 调用 / 凭据加载 / chunking / 错误处理在 :mod:`._transport`
里统一实现, 入口 B (``Channel.send_text`` / ``Channel.edit_text``)
走的是同一份底层. 之前两条路径各自维护一份 raw HTTP + chunking, 改造
后只剩这层 thin wrapper.

设计背景见 ``docs/design/channel-audit.md`` 第 5.F 节.
"""
from __future__ import annotations

from openprogram.channels import _transport


def send(channel: str, account_id: str, user_id: str, text: str) -> bool:
    """Deliver ``text`` to (channel, account_id, user_id). Returns True
    on success.

    "成功" 定义: ``_transport.post_message`` 没返回 None — 即 HTTP 拿到
    2xx 且 platform 业务码 OK. WeChat 在成功时返回空字符串 sentinel
    (iLink 不给可用 message_id), 同样满足 ``is not None``.
    """
    if not text:
        return True
    return _transport.post_message(channel, account_id, user_id, text) is not None
