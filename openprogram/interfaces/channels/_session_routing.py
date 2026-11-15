"""Channel session 路由 — 把 (channel, account, peer) 算成 session_key.

两步:
1. ``session_key_for_agent``: 按 agent 的 ``session_scope`` 决定基础 key
2. ``apply_reset_policy``: 应用 daily / idle 重置策略, 可能在 key 上加
   后缀让下一轮开新 session

session_scope 枚举继承自 OpenClaw 的 dmScope:

  main                      — 一个共享 session 给所有 DM
  per-peer                  — 每个 sender 一个, 跨 channel
  per-channel-peer          — 每个 (channel, sender) 一个
  per-account-channel-peer  — 每个 (account, channel, sender) 一个 (默认)

Group / channel peer 永远按 peer_id 隔离 (跨群共享 session 没人想要).

从 ``_conversation.py`` 拆分出来 — 该文件原本 588 行混了 5 个职责.
这里只承担 session_key 路由这一块.
"""
from __future__ import annotations

import json as _json
import re
import threading
import time
from typing import Any
from openprogram.host._platform.filesystem import replace_file


def session_key_for_agent(
    agent, channel: str, account_id: str, peer: dict[str, Any],
) -> str:
    """按 agent 的 ``session_scope`` 计算 session-routing key.

    默认 agent (``main``) 的 key 不带 agent id — 保持已有会话的 key 不变;
    其他 agent 的 key 前缀 agent id, 两个 agent 都用 ``main`` /
    ``per-peer`` 时不会落进同一个会话.
    """
    from openprogram.runtime.agent_registry.manager import DEFAULT_AGENT_ID

    kind = str(peer.get("kind") or "direct")
    pid = str(peer.get("id") or "")
    scope = getattr(agent, "session_scope", None) or "per-account-channel-peer"

    if kind in ("group", "channel"):
        raw = f"{channel}_{account_id}_{kind}_{pid}"
    elif scope == "main":
        raw = "main"
    elif scope == "per-peer":
        raw = f"peer_{pid}"
    elif scope == "per-channel-peer":
        raw = f"{channel}_{kind}_{pid}"
    else:  # per-account-channel-peer (default)
        raw = f"{account_id}_{kind}_{pid}"

    agent_id = str(getattr(agent, "id", "") or DEFAULT_AGENT_ID)
    if agent_id != DEFAULT_AGENT_ID:
        raw = f"{agent_id}_{raw}"

    safe = re.sub(r"[^A-Za-z0-9_-]", "-", raw).strip("-")
    return safe or "unknown"


def current_session_key(agent, base_key: str) -> str:
    """The key this peer is on right now, starting no new generation."""
    return _with_daily(agent, _with_generation(agent, base_key))


def apply_reset_policy(agent, base_key: str) -> str:
    """应用 ``/new`` 代数和 agent 的 daily / idle 重置策略, 返回最终
    session_key.

    代数: ``/new`` (或 idle 超时) 把 (agent, base_key) 的代数加一, key
    后挂 ``_n{代数}`` — 之后每条消息都落到这个新 session, 直到下次
    重置. 代数持久化在 ``<state>/channels/session_generations.json``.

    Daily reset: ``agent.session_daily_reset`` 是 ``HH:MM`` 时, 在 key
    后面挂当前 reset-window 的日期 — 到点自动新 session.

    Idle reset: ``agent.session_idle_minutes > 0`` 时, 看当前 session
    在 SessionDB 里的 ``updated_at``; 超过阈值就把代数加一, 下一回合
    起写到新 session.

    重置后缀对 UI 透明 — 之前的 session 仍在库里 (sidebar 能读), 新的
    从零开始.
    """
    key = _with_daily(agent, _with_generation(agent, base_key))
    idle_min = int(getattr(agent, "session_idle_minutes", 0) or 0)
    if idle_min > 0:
        last = _last_activity(key)
        if last and (time.time() - last) > idle_min * 60:
            start_new_generation(agent, base_key)
            key = _with_daily(agent, _with_generation(agent, base_key))
    # A deleted conversation never comes back: its id keeps a delete
    # mark, and a turn routed to it has no session to write to. The
    # peer's next message starts a new one, as after ``/new``.
    for _ in range(100):
        if not _session_deleted(key):
            break
        start_new_generation(agent, base_key)
        key = _with_daily(agent, _with_generation(agent, base_key))
    return key


def start_new_generation(agent, base_key: str) -> int:
    """``/new``: 让 (agent, base_key) 之后的消息进一个新 session."""
    ident = f"{agent.id}:{base_key}"
    with _GEN_LOCK:
        table = _read_generations()
        table[ident] = int(table.get(ident) or 0) + 1
        path = _generations_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(_json.dumps(table, indent=2, sort_keys=True),
                       encoding="utf-8")
        replace_file(tmp, path)
        return table[ident]


_GEN_LOCK = threading.Lock()


def _generations_path():
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / "channels" / "session_generations.json"


def _read_generations() -> dict[str, Any]:
    try:
        raw = _json.loads(_generations_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _with_generation(agent, base_key: str) -> str:
    gen = int(_read_generations().get(f"{agent.id}:{base_key}") or 0)
    return f"{base_key}_n{gen}" if gen > 0 else base_key


def _with_daily(agent, key: str) -> str:
    import datetime as _dt

    daily = (getattr(agent, "session_daily_reset", "") or "").strip()
    if not daily:
        return key
    try:
        h, m = daily.split(":", 1)
        reset_h, reset_m = int(h), int(m)
        now = _dt.datetime.now()
        window_start = now.replace(
            hour=reset_h, minute=reset_m, second=0, microsecond=0,
        )
        if now < window_start:
            window_start -= _dt.timedelta(days=1)
        return f"{key}_{window_start.strftime('%Y%m%d')}"
    except (ValueError, AttributeError):
        return key


def _session_deleted(session_key: str) -> bool:
    try:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store.session.placement import is_deleted
        return is_deleted(default_db().root_path, session_key)
    except Exception:  # noqa: BLE001
        return False


def _last_activity(session_key: str) -> float:
    try:
        from openprogram.runtime.context.session_db import default_db
        row = default_db().get_session(session_key) or {}
        return float(row.get("updated_at") or 0)
    except Exception:  # noqa: BLE001
        return 0.0
