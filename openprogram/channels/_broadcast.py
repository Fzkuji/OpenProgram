"""Channel turn 完成后给 webui 推 WS event — best-effort.

两类 envelope:

* ``channel_turn``: 一次完整的 (user msg, assistant reply) 二元组, TUI
  在订阅同 session 时直接 append 到 transcript, 不用 /resume 刷新.
* ``agent_session_updated``: 一份精简的 session-touched 通知, webui
  sidebar 收到后把这个 conversation 顶到列表上方.

步 4：不再 import webui — 改成把现成的 WS 帧 emit 到总线（``ws.frame`` 事件），
webui 作为订阅者原样广播。帧 type/data 字段一字不变；WS push 仍只是 nice-to-have
（持久化已在 SessionDB 完成），emit_ws_frame 内部吞掉一切失败.

从 ``_conversation.py`` 拆分出来 — 该文件原本 588 行混了 5 个职责.
这里只承担 broadcast 这一块.
"""
from __future__ import annotations

from typing import Any

from openprogram.agent.event_bus import emit_ws_frame


def broadcast_channel_turn(
    agent_id: str, session_key: str,
    user_msg: dict[str, Any],
    reply_msg: dict[str, Any],
) -> None:
    """推一对 (user msg, assistant reply) 给所有 WS 客户端.

    TUI consumer (cli_ink) 听到这个事件且 session_id 跟当前 view 的
    session 匹配时, 直接把两条消息追加到 transcript — 这样比如 wechat
    用户发"hello"时, 挂着的 ``openprogram`` TUI 实时显示这通对话, 不
    需要 /resume 刷新. session_key 在两边是同一个标识 (channel +
    webui 共用同套), 不需要翻译.
    """
    emit_ws_frame({
        "type": "channel_turn",
        "data": {
            "session_id": session_key,
            "agent_id": agent_id,
            "user": {
                "id": user_msg.get("id"),
                "text": user_msg.get("content"),
                "peer_display": user_msg.get("peer_display"),
                "source": user_msg.get("source"),
            },
            "assistant": {
                "id": reply_msg.get("id"),
                "text": reply_msg.get("content"),
                "source": reply_msg.get("source"),
            },
        },
    })


def poke_live_webui(
    agent_id: str, session_key: str,
    meta: dict[str, Any],
    messages: list[dict[str, Any]],
) -> None:
    """通知所有 WS 客户端某 channel session 有更新.

    任何失败都安静吞掉 — 持久化已经走完, live push 只是 nicety.
    """
    emit_ws_frame({
        "type": "agent_session_updated",
        "data": {
            "agent_id": agent_id,
            "session_id": session_key,
            "title": meta.get("title"),
            "head_id": meta.get("head_id"),
            "updated_at": meta.get("_last_touched"),
            "source": meta.get("channel"),
        },
    })
