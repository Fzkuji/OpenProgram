"""群聊房间层 — 决定一条群消息是开启一轮, 还是先记下来.

聊天软件的群里大多数消息是人和人在聊, 不是对机器人说的. 这一层对每个
房间 (platform, account, chat_id) 持有:

* **activation** — ``mention``: 只有 @机器人 / 回复机器人才开启一轮;
  ``always``: 每条都开启; ``ambient``: 没点名的消息也开启一轮, 但作为
  房间事件 (文本顶上标 :data:`ROOM_EVENT_HEADER`), 最终回复不发进群,
  agent 只能用 ``chat_send`` 工具开口. 默认取账号设置
  ``require_mention``, 房间里用 ``/activation`` 覆盖.
* **memory** — ``on`` / ``read`` / ``off``: 这个房间的轮次能不能读、写
  主人的记忆. 默认 ``on`` (沿用 agent 自己的记忆设置), ``/memory`` 收窄.
  记忆读取会把主人的记忆带进群里, 群里可能有只能聊天的人.

* **名单覆盖** — 这个房间自己的 ``group_full_access`` / ``group_chat_only``
  (平台 user id 列表). 只要出现过, 这个房间就不再看账号级名单 (见
  :mod:`._access`). 只由本机 CLI 改, 聊天消息碰不到.

覆盖值持久化在账号目录的 ``rooms.json``.
* **pending history** — 被门槛拦下 (或超出限流) 的消息带着发言人留在
  内存里, 下一轮开启时作为引用块交给 agent, 用掉即清. 最多
  :data:`HISTORY_LIMIT` 条.
* **限流** — 一个房间 :data:`TURN_WINDOW_SECONDS` 内最多开启
  :data:`TURN_LIMIT` 轮, 超出的消息落进 pending history; 防两个机器人
  在群里互相回复刷屏.
* **去重** — 同一条平台消息 (Slack 对一次提及同时推 ``message`` 和
  ``app_mention``) 只处理一次.

私聊永远开启, 不经过门槛和限流.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass

HISTORY_LIMIT = 50
TURN_LIMIT = 10
TURN_WINDOW_SECONDS = 60.0
SEEN_LIMIT = 512

ACTIVATION_MODES = ("mention", "always", "ambient")
MEMORY_MODES = ("on", "read", "off")

#: 群聊访问级别 → 名单的存储 key (账号级在 access.json, 房间覆盖在 rooms.json).
GROUP_USER_KEYS = {"full": "group_full_access", "chat": "group_chat_only"}

#: 文本命令. 群里不 @机器人 也生效 (命令本身就是对机器人说的).
COMMANDS = ("/new", "/reset", "/activation", "/memory", "/answer", "/decline",
            "/stop", "/help")

HELP_TEXT = "\n".join((
    "可用命令：",
    "/stop 停止正在进行的任务",
    "/new 或 /reset 开始新对话",
    "/activation 查看或切换群里的回应方式（仅群聊）",
    "/memory 查看或收窄这个群的记忆（仅群聊）",
    "/answer <问题编号> <选项> 回答提问或审批",
    "/decline <问题编号> 拒绝提问或审批",
    "/help 显示这份列表",
))

PENDING_HEADER = "[earlier messages in this chat]"
#: ambient 房间里没点名机器人的消息, 文本第一行是这个.
ROOM_EVENT_HEADER = "[room event]"

_lock = threading.Lock()
_pending: dict[tuple[str, str, str], deque] = {}
_turns: dict[tuple[str, str, str], deque] = {}
_seen: dict[tuple[str, str], deque] = {}


def is_room(chat_type: str) -> bool:
    return chat_type in ("group", "channel", "thread")


@dataclass(frozen=True)
class PendingEntry:
    speaker_id: str
    speaker_display: str
    text: str
    ts: float


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def command_verb(text: str) -> str:
    """The command verb ``text`` opens with (``"/new"``), or ``""``."""
    head = (text or "").strip().split(maxsplit=1)
    if not head:
        return ""
    verb = head[0].lower()
    return verb if verb in COMMANDS else ""


def command_arg(text: str) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def _rooms_path(platform: str, account_id: str):
    from openprogram.interfaces.channels.accounts import account_dir
    return account_dir(platform, account_id) / "rooms.json"


def _read_rooms(platform: str, account_id: str) -> dict:
    try:
        raw = json.loads(_rooms_path(platform, account_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def default_activation(platform: str, account_id: str) -> str:
    from openprogram.interfaces.channels.accounts import get_settings
    on = get_settings(platform, account_id).get("require_mention", "off") == "on"
    return "mention" if on else "always"


def _room_value(platform: str, account_id: str, chat_id: str, key: str):
    room = _read_rooms(platform, account_id).get(str(chat_id))
    return room.get(key) if isinstance(room, dict) else None


def activation(platform: str, account_id: str, chat_id: str) -> str:
    mode = _room_value(platform, account_id, chat_id, "activation")
    return mode if mode in ACTIVATION_MODES else default_activation(platform, account_id)


def set_activation(platform: str, account_id: str, chat_id: str, mode: str) -> None:
    if mode not in ACTIVATION_MODES:
        raise ValueError(f"activation must be one of {ACTIVATION_MODES}")
    _set_room_value(platform, account_id, chat_id, "activation", mode)


def memory_mode(platform: str, account_id: str, chat_id: str) -> str:
    mode = _room_value(platform, account_id, chat_id, "memory")
    return mode if mode in MEMORY_MODES else "on"


def set_memory_mode(platform: str, account_id: str, chat_id: str, mode: str) -> None:
    if mode not in MEMORY_MODES:
        raise ValueError(f"memory must be one of {MEMORY_MODES}")
    _set_room_value(platform, account_id, chat_id, "memory", mode)


def memory_override(mode: str) -> dict | None:
    """Room memory mode → ``TurnRequest.memory_policy_override`` (only narrows)."""
    return {"off": {"mode": "off"}, "read": {"mode": "read_only"}}.get(mode)


def _write_rooms(platform: str, account_id: str, rooms: dict) -> None:
    from openprogram.models.auth.credentials import _private_atomic_write
    from openprogram.state.paths import get_state_dir

    payload = json.dumps(rooms, indent=2, sort_keys=True).encode("utf-8")
    _private_atomic_write(
        _rooms_path(platform, account_id),
        lambda handle: handle.write(payload),
        root=get_state_dir(),
    )


@contextmanager
def _rooms_file_lock(platform: str, account_id: str):
    """Held across a read-modify-write: the CLI and the worker both write rooms.json."""
    from openprogram.models.auth.credentials import _private_file_lock
    from openprogram.state.paths import get_state_dir

    with _private_file_lock(_rooms_path(platform, account_id), root=get_state_dir(), timeout=10):
        yield


def _set_room_value(platform: str, account_id: str, chat_id: str,
                    key: str, value: str) -> None:
    with _rooms_file_lock(platform, account_id):
        rooms = _read_rooms(platform, account_id)
        room = rooms.get(str(chat_id))
        room = dict(room) if isinstance(room, dict) else {}
        room[key] = value
        rooms[str(chat_id)] = room
        _write_rooms(platform, account_id, rooms)


# ---------------------------------------------------------------------------
# Per-room user lists
# ---------------------------------------------------------------------------

def _user_ids(value) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def room_user_lists(platform: str, account_id: str, chat_id: str) -> dict[str, list[str]] | None:
    """This room's own lists by storage key, or None when it has none."""
    room = _read_rooms(platform, account_id).get(str(chat_id))
    if not isinstance(room, dict) or not any(key in room for key in GROUP_USER_KEYS.values()):
        return None
    return {key: _user_ids(room.get(key)) for key in GROUP_USER_KEYS.values()}


def set_room_user(platform: str, account_id: str, chat_id: str,
                  user_id: str, access: str) -> None:
    """Put ``user_id`` on this room's ``access`` list, off the other one."""
    with _rooms_file_lock(platform, account_id):
        rooms = _read_rooms(platform, account_id)
        room = rooms.get(str(chat_id))
        room = dict(room) if isinstance(room, dict) else {}
        for level, key in GROUP_USER_KEYS.items():
            kept = [uid for uid in _user_ids(room.get(key)) if uid != user_id]
            room[key] = sorted([*kept, user_id]) if level == access else kept
        rooms[str(chat_id)] = room
        _write_rooms(platform, account_id, rooms)


def remove_room_user(platform: str, account_id: str, chat_id: str, user_id: str) -> bool:
    """Take ``user_id`` off this room's lists. The lists stay, even when empty."""
    with _rooms_file_lock(platform, account_id):
        rooms = _read_rooms(platform, account_id)
        room = rooms.get(str(chat_id))
        if not isinstance(room, dict):
            return False
        room = dict(room)
        removed = False
        for key in GROUP_USER_KEYS.values():
            if key in room and user_id in _user_ids(room[key]):
                room[key] = [uid for uid in _user_ids(room[key]) if uid != user_id]
                removed = True
        if removed:
            rooms[str(chat_id)] = room
            _write_rooms(platform, account_id, rooms)
        return removed


def drop_room_chat_user(platform: str, account_id: str, user_id: str) -> None:
    """Take ``user_id`` off every room's chat list."""
    key = GROUP_USER_KEYS["chat"]
    with _rooms_file_lock(platform, account_id):
        rooms = _read_rooms(platform, account_id)
        changed = False
        for chat_id, room in rooms.items():
            if isinstance(room, dict) and user_id in _user_ids(room.get(key)):
                rooms[chat_id] = {**room, key: [uid for uid in _user_ids(room[key]) if uid != user_id]}
                changed = True
        if changed:
            _write_rooms(platform, account_id, rooms)


def clear_room_users(platform: str, account_id: str, chat_id: str) -> bool:
    """Drop this room's own lists, so the account's lists apply again."""
    with _rooms_file_lock(platform, account_id):
        rooms = _read_rooms(platform, account_id)
        room = rooms.get(str(chat_id))
        if not isinstance(room, dict) or not any(key in room for key in GROUP_USER_KEYS.values()):
            return False
        room = {key: value for key, value in room.items() if key not in GROUP_USER_KEYS.values()}
        rooms[str(chat_id)] = room
        _write_rooms(platform, account_id, rooms)
        return True


# ---------------------------------------------------------------------------
# Pending history
# ---------------------------------------------------------------------------

def record_pending(key: tuple[str, str, str], entry: PendingEntry) -> None:
    with _lock:
        queue = _pending.get(key)
        if queue is None:
            queue = _pending[key] = deque(maxlen=HISTORY_LIMIT)
        queue.append(entry)


def take_pending(key: tuple[str, str, str]) -> list[PendingEntry]:
    with _lock:
        queue = _pending.pop(key, None)
    return list(queue or ())


def restore_pending(key: tuple[str, str, str], entries: list[PendingEntry]) -> None:
    """Put back entries a turn took but never ran, ahead of anything newer."""
    with _lock:
        newer = list(_pending.get(key) or ())
        _pending[key] = deque([*entries, *newer], maxlen=HISTORY_LIMIT)


def clear_pending(key: tuple[str, str, str]) -> None:
    with _lock:
        _pending.pop(key, None)


def render_pending(entries: list[PendingEntry]) -> str:
    """Pending messages as one quoted block, one JSON object per line.

    JSON keeps every message on its own physical line (newlines in the
    text are escaped), so a message cannot start a line that reads like
    another speaker.
    """
    from openprogram.host.security.identity_header import normalize_identity_header_part as _clean

    lines = [PENDING_HEADER]
    for entry in entries:
        display, sid = _clean(entry.speaker_display), _clean(entry.speaker_id)
        speaker = f"{display} ({sid})" if display and sid and display != sid else (display or sid)
        lines.append("> " + json.dumps(
            {"speaker": speaker, "content": entry.text},
            ensure_ascii=False, separators=(",", ":"),
        ))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Flood limit and duplicate delivery
# ---------------------------------------------------------------------------

def allow_turn(key: tuple[str, str, str], now: float | None = None) -> bool:
    """Count a turn for this room; False once the window is full."""
    now = time.time() if now is None else now
    with _lock:
        stamps = _turns.get(key)
        if stamps is None:
            stamps = _turns[key] = deque()
        while stamps and now - stamps[0] > TURN_WINDOW_SECONDS:
            stamps.popleft()
        if len(stamps) >= TURN_LIMIT:
            return False
        stamps.append(now)
        return True


def give_back_turn(key: tuple[str, str, str]) -> None:
    """Undo one :func:`allow_turn` for a message that turned out to start nothing."""
    with _lock:
        stamps = _turns.get(key)
        if stamps:
            stamps.pop()


def first_delivery(platform: str, account_id: str, chat_id: str, message_id: str) -> bool:
    """True the first time a platform message is seen; messages without
    an id always count as new."""
    if not message_id:
        return True
    key = (platform, account_id)
    ident = f"{chat_id}\x00{message_id}"
    with _lock:
        seen = _seen.get(key)
        if seen is None:
            seen = _seen[key] = deque(maxlen=SEEN_LIMIT)
        if ident in seen:
            return False
        seen.append(ident)
        return True


def reset_state() -> None:
    """Forget all in-memory room state (tests)."""
    with _lock:
        _pending.clear()
        _turns.clear()
        _seen.clear()
