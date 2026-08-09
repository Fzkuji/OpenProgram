"""入站 access 门禁 — 每个 channel 账号一份 allowlist + pairing 状态.

未知发信人的消息不驱动 agent. ``base.Channel._dispatch_and_reply`` 在
路由 / dispatch 之前调 :func:`check_inbound`; 不在 allowlist 里的发信
人拿到一个配对码, 消息本身被丢弃. 机主在本机确认:

    openprogram channels access approve telegram ABC123
    openprogram channels access list
    openprogram channels access revoke telegram 123456
    openprogram channels access policy telegram open

安全边界: **approve / revoke / set_policy 只能由本机进程调用 (CLI /
webui), 永远不接 channel 消息触发**. check_inbound 是入站路径上唯一
入口, 它只读 allowlist、只写 pending 表 — 一条精心构造的消息最多给
自己刷一个配对码, 不可能把自己放进 allowlist. /answer 等文本命令在
dispatch 里处理, 而 dispatch 只对已放行的发信人运行, 顺序上也到不了.

存储: ``<state>/channels/<channel>/accounts/<account_id>/access.json``

    {
      "policy": "pairing",              # "pairing" (默认) | "open"
      "allowlist": {"<user_id>": {"display": ..., "approved_at": ts}},
      "pending":   {"<user_id>": {"code": ..., "display": ...,
                                   "requested_at": ts, "notified_at": ts}}
    }

policy:
  * ``pairing`` — 默认. 未知发信人首次来信生成配对码并回执说明; 机主
    approve 后放行. 配对码 1 小时过期, 过期后下一条消息刷新.
  * ``open``    — 不设防, 任何发信人直接进 agent (机主显式选择).

多人共用: allowlist 放多少人都行, 一个群里的每个人都可以批准. 他们共
用同一个 agent 和同一份记忆工作区 (``<state>/memory/``), 记忆按发言人
记录谁说了什么 —— 每条 user 消息带 ``peer_display`` 和 ``sender_id``,
一路带到 ``memory/scriptorium`` 的 ``SourceRecord``. 门禁判定的是"这
个人能不能进", 不是"能进几个人".
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from openprogram.channels import accounts as _accounts


DEFAULT_POLICY = "pairing"
POLICIES = ("pairing", "open")

#: 配对码字符集 — 去掉易混淆的 0/O/1/I.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 6
#: pending 配对码有效期 (秒).
PENDING_TTL = 3600.0
#: 同一发信人两次配对回执之间的最短间隔 (秒) — 防止跟另一个 bot 互刷.
_NOTIFY_INTERVAL = 60.0

_lock = threading.RLock()


def access_path(channel: str, account_id: str) -> Path:
    return _accounts.account_dir(channel, account_id) / "access.json"


def _load(channel: str, account_id: str) -> dict[str, Any]:
    path = access_path(channel, account_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    policy = raw.get("policy")
    if policy not in POLICIES:
        policy = DEFAULT_POLICY
    allowlist = raw.get("allowlist")
    pending = raw.get("pending")
    return {
        "policy": policy,
        "allowlist": dict(allowlist) if isinstance(allowlist, dict) else {},
        "pending": dict(pending) if isinstance(pending, dict) else {},
    }


def _save(channel: str, account_id: str, data: dict[str, Any]) -> None:
    path = access_path(channel, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _prune_pending(data: dict[str, Any], now: float) -> None:
    expired = [
        uid for uid, row in data["pending"].items()
        if now - float(row.get("requested_at") or 0) > PENDING_TTL
    ]
    for uid in expired:
        del data["pending"][uid]


# ---------------------------------------------------------------------------
# 入站路径 (只读 allowlist / 只写 pending)
# ---------------------------------------------------------------------------

def check_inbound(
    channel: str, account_id: str, user_id: str, display: str = "",
) -> tuple[bool, Optional[str]]:
    """入站门禁判定. 返回 ``(allowed, reply)``.

    * allowed=True  → 消息照常进 dispatch, reply 恒为 None.
    * allowed=False → 消息丢弃; reply 非 None 时 adapter 把它发回给
      发信人 (配对码说明), None 表示这次静默 (60s 内已经回执过).
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return False, None
    with _lock:
        data = _load(channel, account_id)
        if data["policy"] == "open" or user_id in data["allowlist"]:
            return True, None

        now = time.time()
        _prune_pending(data, now)
        row = data["pending"].get(user_id)
        if row is None:
            row = {
                "code": _new_code(),
                "display": display or user_id,
                "requested_at": now,
                "notified_at": 0.0,
            }
        notify = now - float(row.get("notified_at") or 0) >= _NOTIFY_INTERVAL
        if notify:
            row["notified_at"] = now
        if display:
            row["display"] = display
        data["pending"][user_id] = row
        _save(channel, account_id, data)

    if not notify:
        return False, None
    return False, (
        "This bot only talks to approved contacts.\n"
        f"Your pairing code: {row['code']}\n"
        "Ask the owner to approve you on their machine:\n"
        f"  openprogram channels access approve {channel} {row['code']}"
        + _id_flag(account_id)
    )


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
    with _lock:
        data = _load(channel, account_id)
        _prune_pending(data, time.time())
        for uid, row in list(data["pending"].items()):
            if str(row.get("code", "")).upper() == code:
                del data["pending"][uid]
                data["allowlist"][uid] = {
                    "display": row.get("display") or uid,
                    "approved_at": time.time(),
                }
                _save(channel, account_id, data)
                return uid
        return None


def approve_user(channel: str, account_id: str, user_id: str,
                 display: str = "") -> None:
    """直接把 platform user id 加进 allowlist (机主已知 id 时不必等
    对方来信刷配对码).

    allowlist 里已经有别人不影响 — 一个账号可以放行任意多个发信人.
    """
    user_id = str(user_id).strip()
    if not user_id:
        raise ValueError("empty user id")
    with _lock:
        data = _load(channel, account_id)
        pending = data["pending"].pop(user_id, None)
        data["allowlist"][user_id] = {
            "display": display or (pending or {}).get("display") or user_id,
            "approved_at": time.time(),
        }
        _save(channel, account_id, data)


def revoke(channel: str, account_id: str, user_id: str) -> bool:
    """把 user id 移出 allowlist (兼清 pending). 返回是否真的删了."""
    user_id = str(user_id).strip()
    with _lock:
        data = _load(channel, account_id)
        removed = data["allowlist"].pop(user_id, None) is not None
        removed = data["pending"].pop(user_id, None) is not None or removed
        if removed:
            _save(channel, account_id, data)
        return removed


def set_policy(channel: str, account_id: str, policy: str) -> None:
    if policy not in POLICIES:
        raise ValueError(
            f"unknown policy {policy!r} — expected one of {POLICIES}"
        )
    with _lock:
        data = _load(channel, account_id)
        data["policy"] = policy
        _save(channel, account_id, data)


def describe(channel: str, account_id: str) -> dict[str, Any]:
    """完整 access 状态 (policy + allowlist + 未过期 pending) — CLI
    `channels access list` / webui 展示用."""
    with _lock:
        data = _load(channel, account_id)
        _prune_pending(data, time.time())
        return data
