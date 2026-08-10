"""入站 access 门禁 — 每个 channel 账号一份 allowlist + pairing 状态.

未知发信人的消息不驱动 agent. ``base.Channel._dispatch_and_reply`` 在
路由 / dispatch 之前调 :func:`check_inbound`; 不在 allowlist 里的发信
人拿到一个配对码, 消息本身被丢弃. 机主在本机确认:

    openprogram channels access approve telegram ABCD2345
    openprogram channels access list
    openprogram channels access revoke telegram 123456

安全边界: **approve / revoke 只能由本机进程调用 (CLI /
webui), 永远不接 channel 消息触发**. check_inbound 是入站路径上唯一
入口, 它只读 allowlist、只写 pending 表 — 一条精心构造的消息最多给
自己刷一个配对码, 不可能把自己放进 allowlist. /answer 等文本命令在
dispatch 里处理, 而 dispatch 只对已放行的发信人运行, 顺序上也到不了.

存储: ``<state>/channels/<channel>/accounts/<account_id>/access.json``

    {
      "policy": "pairing",
      "allowlist": {"<user_id>": {"display": ..., "approved_at": ts}},
      "pending":   {"<user_id>": {"code": ..., "display": ...,
                                   "requested_at": ts, "notified_at": ts}}
    }

未知发信人首次来信生成配对码并回执说明; 机主 approve 后放行. 配对码
1 小时过期, 过期后下一条消息刷新. 不存在关闭认证或开放入站的策略.

多人共用: allowlist 放多少人都行, 一个群里的每个人都可以由机主分别
批准. 他们共用同一个 agent 和同一份记忆工作区 (``<state>/memory/``), 记忆按发言人
记录谁说了什么 —— 每条渠道 user 消息带 ``speaker_display`` 和 ``speaker_id``,
一路带到 ``memory/scriptorium`` 的 ``SourceRecord``. 门禁判定的是"这
个人能不能进", 不是"能进几个人".
"""
from __future__ import annotations

import json
import logging
import math
import os
import secrets
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from openprogram import _compat as fcntl
from openprogram.channels import accounts as _accounts


DEFAULT_POLICY = "pairing"

#: 配对码字符集 — 去掉易混淆的 0/O/1/I.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
#: pending 配对码有效期 (秒).
PENDING_TTL = 3600.0
#: 单个 channel account 同时保留的待配对请求上限.
MAX_PENDING = 3

_lock = threading.RLock()
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    admission: str
    check: str
    reason_code: str
    reply: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decision(
    allowed: bool,
    admission: str,
    reason_code: str,
    reply: Optional[str] = None,
) -> AccessDecision:
    value = AccessDecision(
        allowed, admission, "stable_sender_allowlist", reason_code, reply,
    )
    _log.info("channel admission decision %s", value.to_dict())
    return value


def _safe_display(value: str) -> str:
    from openprogram._text import normalize_identity_header_part

    return normalize_identity_header_part(str(value or ""))


def access_path(channel: str, account_id: str) -> Path:
    return _accounts.account_dir(channel, account_id) / "access.json"


@contextmanager
def _state_file_lock(channel: str, account_id: str):
    path = access_path(channel, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".json.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "policy": DEFAULT_POLICY,
        "allowlist": _rows(raw.get("allowlist")),
        "pending": _rows(raw.get("pending")),
    }


def _save(channel: str, account_id: str, data: dict[str, Any]) -> None:
    path = access_path(channel, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="access-", suffix=".json.tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _prune_pending(data: dict[str, Any], now: float) -> None:
    expired = []
    for uid, row in data["pending"].items():
        try:
            requested_at = float(row.get("requested_at") or 0)
        except (TypeError, ValueError):
            requested_at = 0.0
        if not math.isfinite(requested_at) \
                or now - requested_at > PENDING_TTL:
            expired.append(uid)
    for uid in expired:
        del data["pending"][uid]


# ---------------------------------------------------------------------------
# 入站路径 (只读 allowlist / 只写 pending)
# ---------------------------------------------------------------------------

def check_inbound(
    channel: str, account_id: str, user_id: str, display: str = "",
) -> AccessDecision:
    """Return one structured admission decision for a stable sender ID.

    * ``allowed=True`` → 消息照常进 dispatch, ``reply`` 恒为 None.
    * ``allowed=False`` → 消息丢弃; ``reply`` 非 None 时 adapter 把它发回给
      发信人 (配对码说明), None 表示同一小时内已经回执过或 pending 已满.
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return _decision(
            False, "unpaired", "STABLE_SENDER_ID_MISSING",
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
                False, "unpaired", "PAIRING_ALREADY_PENDING",
            )
        if len(data["pending"]) >= MAX_PENDING:
            return _decision(
                False, "unpaired", "PAIRING_PENDING_LIMIT",
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
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        pending = data["pending"].pop(user_id, None)
        data["allowlist"][user_id] = {
            "display": _safe_display(
                display or (pending or {}).get("display") or user_id
            ),
            "approved_at": time.time(),
        }
        _save(channel, account_id, data)


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


def describe(channel: str, account_id: str) -> dict[str, Any]:
    """完整 access 状态 (policy + allowlist + 未过期 pending) — CLI
    `channels access list` / webui 展示用."""
    with _lock, _state_file_lock(channel, account_id):
        data = _load(channel, account_id)
        _prune_pending(data, time.time())
        return data
