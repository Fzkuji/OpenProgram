"""正在跑的渠道轮次 — 会话 → 这一轮来自哪个聊天、谁发起的.

一个会话同一时刻只跑一轮 (:mod:`._conversation` 的会话锁), 所以按
session_key 登记就能唯一找到"当前这一轮". 两个读者:

* ``chat_send`` 工具 — 工具里只知道 session id; 回到哪个聊天要看这一轮
  的 peer, 不能看会话行 (``session_scope: main`` 把多个 peer 放进同一个
  会话, 会话行只记得第一个).
* 群里的 ``/answer`` · ``/decline`` — 一轮停在 runtime.ask 时, 只有发起
  这一轮的人能回答它的问题.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

#: 一轮最多用 ``chat_send`` 发几条, 防止模型刷屏.
MAX_SENDS_PER_TURN = 5


@dataclass(frozen=True)
class ActiveTurn:
    channel: str
    account_id: str
    peer_id: str
    peer_kind: str
    speaker_id: str
    #: 群里没点名机器人的消息 (ambient 模式): 最终回复不发到聊天里.
    ambient: bool = False
    #: 这一轮所在的 thread (Slack 帖子 / Telegram 论坛话题), 没有就是空串.
    thread_id: str = ""


_lock = threading.Lock()
_turns: dict[str, ActiveTurn] = {}
_sends: dict[str, int] = {}


def begin(session_key: str, turn: ActiveTurn) -> None:
    with _lock:
        _turns[session_key] = turn
        _sends[session_key] = 0


def end(session_key: str) -> None:
    with _lock:
        _turns.pop(session_key, None)
        _sends.pop(session_key, None)


def get(session_key: str) -> ActiveTurn | None:
    with _lock:
        return _turns.get(session_key)


def sessions_for_peer(channel: str, account_id: str, peer_id: str) -> list[str]:
    """Sessions running a turn that came from this chat."""
    with _lock:
        return [
            session_key for session_key, turn in _turns.items()
            if (turn.channel, turn.account_id, turn.peer_id)
            == (channel, account_id, str(peer_id))
        ]


def take_send(session_key: str) -> bool:
    """Count one ``chat_send``; False once the turn has used its quota."""
    with _lock:
        used = _sends.get(session_key)
        if used is None or used >= MAX_SENDS_PER_TURN:
            return False
        _sends[session_key] = used + 1
        return True
