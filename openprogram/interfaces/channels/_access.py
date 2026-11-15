"""入站 access 门禁 — 每个 channel 账号一份 allowlist + pairing 状态.

未知发信人的消息不驱动 agent. ``base.Channel._dispatch_and_reply`` 在
路由 / dispatch 之前调 :func:`decide_inbound_sender`; 不在 allowlist 里的发信
人拿到一个配对码, 消息本身被丢弃. 机主在本机确认:

    openprogram channels access approve telegram ABCD2345
    openprogram channels access list
    openprogram channels access revoke telegram 123456

安全边界: **approve / revoke 只能由本机进程调用 (CLI /
webui), 永远不接 channel 消息触发**. decide_inbound_sender 是入站路径上唯一
入口, 它只读 allowlist、只写 pending 表 — 一条精心构造的消息最多给
自己刷一个配对码, 不可能把自己放进 allowlist. /answer 等文本命令在
dispatch 里处理, 而 dispatch 只对已放行的发信人运行, 顺序上也到不了.

存储: ``<state>/channels/<channel>/accounts/<account_id>/access.json``

    {
      "policy": "pairing",
      "allowlist": {"<user_id>": {"display": ..., "approved_at": ts}},
      "pending":   {"<user_id>": {"code": ..., "display": ...,
                                   "requested_at": ts, "notified_at": ts}},
      "group_full_access": {"<user_id>": {"display": ..., "added_at": ts}},
      "group_chat_only":   {"<user_id>": {"display": ..., "added_at": ts}}
    }

群聊不走配对. :func:`group_sender_access` 只读, 三种结果: 绑定的联系人
(allowlist) 和 ``group_full_access`` 名单上的人是 ``full``, 和本人私聊同权;
``group_chat_only`` 上的人是 ``chat``, 只能回复和用记忆; 其余 ``None``,
消息直接丢弃, 不发东西、不生成配对码、不建会话. 某个群可以在 ``rooms.json``
里带自己的两张名单, 这时账号级名单对这个群不起作用, 绑定的联系人不受影响.

未知发信人首次来信生成配对码并回执说明; 机主 approve 后放行. 配对码
1 小时过期, 过期后下一条消息刷新. 不存在关闭认证或开放入站的策略.

多人共用: allowlist 放多少人都行, 上面的人都代表机主. 他们共用同一个 agent 和同一份记忆工作区 (``<state>/memory/``), 记忆按发言人
记录谁说了什么 —— 每条渠道 user 消息带 ``speaker_display`` 和 ``speaker_id``,
一路带到 ``memory/`` 读取的 ``ChatTurn``. 门禁判定的是"这
个人能不能进", 不是"能进几个人".
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from openprogram.interfaces.channels._room import (
    GROUP_USER_KEYS,
    clear_room_users,
    drop_room_chat_user,
    remove_room_user,
    room_user_lists,
    set_room_user,
)

DEFAULT_POLICY = "pairing"

#: 有群聊的渠道 (WeChat iLink 只有私聊).
GROUP_CHANNELS = ("telegram", "discord", "slack")
#: 名单只认平台的稳定 user id; 用户名和显示名能改, 也会撞名.
_GROUP_USER_ID = {
    "telegram": re.compile(r"^[0-9]+$"),
    "discord": re.compile(r"^[0-9]+$"),
    "slack": re.compile(r"^[UW][A-Z0-9]+$"),
}

#: 配对码字符集 — 去掉易混淆的 0/O/1/I.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
_PATH_ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
#: pending 配对码有效期 (秒).
PENDING_TTL = 3600.0
#: 单个 channel account 同时保留的待配对请求上限.
MAX_PENDING = 3

_lock = threading.RLock()
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    pairing_state: str
    check: str
    reason_code: str
    reply: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decision(
    allowed: bool,
    pairing_state: str,
    reason_code: str,
    reply: Optional[str] = None,
) -> AccessDecision:
    value = AccessDecision(
        allowed,
        pairing_state,
        "stable_sender_allowlist",
        reason_code,
        reply,
    )
    _log.info("channel pairing decision %s", value.to_dict())
    return value


def _safe_display(value: str) -> str:
    from openprogram.host.security.identity_header import normalize_identity_header_part

    return normalize_identity_header_part(str(value or ""))


def access_path(channel: str, account_id: str) -> Path:
    if not _PATH_ID.fullmatch(str(channel)):
        raise ValueError("invalid channel id")
    if not _PATH_ID.fullmatch(str(account_id)):
        raise ValueError("invalid channel account id")
    from openprogram.state.paths import get_state_dir

    return (
        get_state_dir()
        / "channels"
        / channel
        / "accounts"
        / account_id
        / "access.json"
    )


@contextmanager
def _state_file_lock(channel: str, account_id: str):
    path = access_path(channel, account_id)
    from openprogram.models.auth.credentials import _private_file_lock
    from openprogram.state.paths import get_state_dir

    with _private_file_lock(path, root=get_state_dir(), timeout=10):
        yield


def _rows(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for raw_user_id, raw_row in value.items():
        user_id = str(raw_user_id).strip()
        if not user_id or not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        row["display"] = _safe_display(row.get("display", ""))
        result[user_id] = row
    return result


def _load(channel: str, account_id: str) -> dict[str, Any]:
    path = access_path(channel, account_id)
    from openprogram.models.auth.credentials import _private_file_lock, _read_private_bytes
    from openprogram.state.paths import get_state_dir

    try:
        with _private_file_lock(path, root=get_state_dir(), timeout=10):
            stored = _read_private_bytes(path, root=get_state_dir())
        raw = json.loads(stored.decode("utf-8")) if stored is not None else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "policy": DEFAULT_POLICY,
        "allowlist": _rows(raw.get("allowlist")),
        "pending": _rows(raw.get("pending")),
        **{key: _rows(raw.get(key)) for key in GROUP_USER_KEYS.values()},
    }


def _save(
    channel: str,
    account_id: str,
    data: dict[str, Any],
    *,
    expected_revision: str | None = None,
):
    path = access_path(channel, account_id)
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    from openprogram.models.auth.credentials import _private_atomic_write
    from openprogram.state.paths import get_state_dir

    return _private_atomic_write(
        path,
        lambda handle: handle.write(payload),
        root=get_state_dir(),
        expected_revision=expected_revision,
    )


def _prune_pending(data: dict[str, Any], now: float) -> None:
    expired = []
    for uid, row in data["pending"].items():
        try:
            requested_at = float(row.get("requested_at") or 0)
        except (TypeError, ValueError):
            requested_at = 0.0
        if not math.isfinite(requested_at) or now - requested_at > PENDING_TTL:
            expired.append(uid)
    for uid in expired:
        del data["pending"][uid]


# ---------------------------------------------------------------------------
# 入站路径 (只读 allowlist / 只写 pending)
# ---------------------------------------------------------------------------


def decide_inbound_sender(
    channel: str,
    account_id: str,
    user_id: str,
    display: str = "",
) -> AccessDecision:
    """Return one structured pairing decision for a stable sender ID.

    * ``allowed=True`` → 消息照常进 dispatch, ``reply`` 恒为 None.
    * ``allowed=False`` → 消息丢弃; ``reply`` 非 None 时 adapter 把它发回给
      发信人 (配对码说明), None 表示同一小时内已经回执过或 pending 已满.
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return _decision(
            False,
            "unpaired",
            "STABLE_SENDER_ID_MISSING",
        )
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        if user_id in data["allowlist"]:
            return _decision(True, "paired", "PAIRED_SENDER")

        now = time.time()
        _prune_pending(data, now)
        row = data["pending"].get(user_id)
        if row is not None:
            if display:
                row["display"] = _safe_display(display)
                data["pending"][user_id] = row
                _save(channel, account_id, data)
            return _decision(
                False,
                "unpaired",
                "PAIRING_ALREADY_PENDING",
            )
        if len(data["pending"]) >= MAX_PENDING:
            return _decision(
                False,
                "unpaired",
                "PAIRING_PENDING_LIMIT",
            )
        row = {
            "code": _new_code(),
            "display": _safe_display(display or user_id),
            "requested_at": now,
            "notified_at": now,
        }
        data["pending"][user_id] = row
        _save(channel, account_id, data)

    reply = (
        "This bot only talks to approved contacts.\n"
        f"Your pairing code: {row['code']}\n"
        "Ask the owner to approve you on their machine:\n"
        f"  openprogram channels access approve {channel} {row['code']}"
        + _id_flag(account_id)
    )
    return _decision(False, "unpaired", "PAIRING_REQUIRED", reply)


def group_sender_access(
    channel: str,
    account_id: str,
    chat_id: str,
    user_id: str,
    *,
    parent_chat_id: str = "",
) -> Optional[str]:
    """What this sender may do in this group: ``full``, ``chat`` or None.

    Reads only: no pending row, no reply, nothing a sender can provoke.
    ``parent_chat_id`` lets a thread use its parent channel's lists when it
    has none of its own.
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return None
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
    if user_id in data["allowlist"]:
        return "full"
    lists = room_user_lists(channel, account_id, chat_id)
    if lists is None and parent_chat_id:
        lists = room_user_lists(channel, account_id, parent_chat_id)
    if lists is None:
        lists = data
    for level, key in GROUP_USER_KEYS.items():
        if user_id in lists[key]:
            return level
    return None


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def _id_flag(account_id: str) -> str:
    """``--id <account>`` 后缀, 默认账号留空 — 回执里的命令能直接抄走."""
    return "" if account_id == "default" else f" --id {account_id}"


# ---------------------------------------------------------------------------
# 本机管理面 (CLI / webui — 永远不由 channel 消息触发)
# ---------------------------------------------------------------------------


def approve(channel: str, account_id: str, code: str) -> Optional[str]:
    """按配对码放行. 返回放行的 user_id, 码不存在/过期返回 None.

    allowlist 里已经有别人不影响 — 一个账号可以放行任意多个发信人.
    """
    code = (code or "").strip().upper()
    if not code:
        return None
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        _prune_pending(data, time.time())
        for uid, row in list(data["pending"].items()):
            if str(row.get("code", "")).upper() == code:
                del data["pending"][uid]
                data["allowlist"][uid] = {
                    "display": _safe_display(row.get("display") or uid),
                    "approved_at": time.time(),
                }
                _drop_chat_listing(channel, account_id, data, uid)
                _save(channel, account_id, data)
                return uid
        return None


def approve_user(
    channel: str, account_id: str, user_id: str, display: str = ""
) -> None:
    """直接把 platform user id 加进 allowlist (机主已知 id 时不必等
    对方来信刷配对码).

    allowlist 里已经有别人不影响 — 一个账号可以放行任意多个发信人.
    """
    user_id = str(user_id).strip()
    if not user_id:
        raise ValueError("empty user id")
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        pending = data["pending"].pop(user_id, None)
        data["allowlist"][user_id] = {
            "display": _safe_display(
                display or (pending or {}).get("display") or user_id
            ),
            "approved_at": time.time(),
        }
        _drop_chat_listing(channel, account_id, data, user_id)
        _save(channel, account_id, data)


def _drop_chat_listing(
    channel: str, account_id: str, data: dict[str, Any], user_id: str
) -> None:
    """A bound contact has full access in groups, so a chat listing would lie."""
    data[GROUP_USER_KEYS["chat"]].pop(user_id, None)
    drop_room_chat_user(channel, account_id, user_id)


def revoke(channel: str, account_id: str, user_id: str) -> bool:
    """把 user id 移出 allowlist (兼清 pending). 返回是否真的删了."""
    user_id = str(user_id).strip()
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        removed = data["allowlist"].pop(user_id, None) is not None
        removed = data["pending"].pop(user_id, None) is not None or removed
        if removed:
            _save(channel, account_id, data)
        return removed


def _group_user_id(channel: str, user_id: str) -> str:
    user_id = str(user_id or "").strip()
    pattern = _GROUP_USER_ID.get(channel)
    if not user_id or (pattern is not None and not pattern.fullmatch(user_id)):
        raise ValueError(
            f"{user_id!r} is not a {channel} user id. Use the numeric or "
            "platform id, not a username or display name."
        )
    return user_id


def add_group_user(
    channel: str,
    account_id: str,
    user_id: str,
    access: str,
    *,
    chat_id: str = "",
    display: str = "",
) -> str:
    """Put a user on a group list; adding them moves them off the other one.

    Without ``chat_id`` this is the account's list. With it, it is that
    group's own list, which replaces the account's lists for that group.
    A bound contact always has full access in groups, so listing one as
    ``chat`` is refused rather than recorded with no effect. Local callers only.
    """
    if access not in GROUP_USER_KEYS:
        raise ValueError(f"access must be one of {tuple(GROUP_USER_KEYS)}")
    user_id = _group_user_id(channel, user_id)
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        if access == "chat" and user_id in data["allowlist"]:
            raise ValueError(
                f"{user_id} is a bound contact on {channel}:{account_id}, and a "
                "bound contact always has full access in groups. To limit them "
                f"to chat, first run `openprogram channels access revoke {channel} "
                f"{user_id}{_id_flag(account_id)}`, which also ends their direct "
                "messages."
            )
        if chat_id:
            set_room_user(channel, account_id, chat_id, user_id, access)
            return user_id
        for key in GROUP_USER_KEYS.values():
            data[key].pop(user_id, None)
        data[GROUP_USER_KEYS[access]][user_id] = {
            "display": _safe_display(display or user_id),
            "added_at": time.time(),
        }
        _save(channel, account_id, data)
    return user_id


def remove_group_user(
    channel: str, account_id: str, user_id: str, *, chat_id: str = "",
) -> bool:
    """Take a user off a group's lists, or the account's. True if they were on one."""
    user_id = str(user_id or "").strip()
    if chat_id:
        return remove_room_user(channel, account_id, chat_id, user_id)
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        removed = False
        for key in GROUP_USER_KEYS.values():
            removed = data[key].pop(user_id, None) is not None or removed
        if removed:
            _save(channel, account_id, data)
        return removed


def reset_group_users(channel: str, account_id: str, chat_id: str) -> bool:
    """Drop a group's own lists, so the account's lists apply to it again."""
    return clear_room_users(channel, account_id, chat_id)


def group_users(channel: str, account_id: str, *, chat_id: str = "") -> dict[str, Any]:
    """The lists in force for the account, or for one group.

    ``own_lists`` says whether the group has lists of its own; rows are
    keyed by user id, as in the account's ``access.json``.
    """
    room = room_user_lists(channel, account_id, chat_id) if chat_id else None
    if room is not None:
        return {
            "own_lists": True,
            **{key: {uid: {"display": ""} for uid in room[key]}
               for key in GROUP_USER_KEYS.values()},
        }
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
    return {
        "own_lists": False,
        **{key: data[key] for key in GROUP_USER_KEYS.values()},
    }


def describe(channel: str, account_id: str) -> dict[str, Any]:
    """完整 access 状态 (policy + allowlist + 未过期 pending) — CLI
    `channels access list` / webui 展示用."""
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        _prune_pending(data, time.time())
        return data
