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
import time
from typing import Any

from openprogram.channels._session_store import meta_path


def session_key_for_agent(
    agent, channel: str, account_id: str, peer: dict[str, Any],
) -> str:
    """按 agent 的 ``session_scope`` 计算 session-routing key."""
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

    safe = re.sub(r"[^A-Za-z0-9_-]", "-", raw).strip("-")
    return safe or "unknown"


def apply_reset_policy(agent, base_key: str) -> str:
    """应用 agent 的 daily / idle session 重置策略, 返回最终 session_key.

    Daily reset: ``agent.session_daily_reset`` 是 ``HH:MM`` 时, 在 key
    后面挂当前 reset-window 的日期 — 到点自动新 session.

    Idle reset: ``agent.session_idle_minutes > 0`` 时, 检查现有 session
    的 ``_last_touched``; 超过阈值就在 key 后面挂 epoch minute, 让下一
    回合写到新文件.

    重置后缀对 UI 透明 — 之前的 session 仍在磁盘 (sidebar 能读), 新的
    从零开始.
    """
    import datetime as _dt

    key = base_key
    daily = (getattr(agent, "session_daily_reset", "") or "").strip()
    if daily:
        try:
            h, m = daily.split(":", 1)
            reset_h, reset_m = int(h), int(m)
            now = _dt.datetime.now()
            window_start = now.replace(
                hour=reset_h, minute=reset_m, second=0, microsecond=0,
            )
            if now < window_start:
                window_start -= _dt.timedelta(days=1)
            key += f"_{window_start.strftime('%Y%m%d')}"
        except (ValueError, AttributeError):
            pass

    idle_min = int(getattr(agent, "session_idle_minutes", 0) or 0)
    if idle_min > 0:
        # 看上一次的 session (base + 可能的 daily 后缀). 过期就加 idle
        # 后缀让它轮换.
        prev_meta = meta_path(agent.id, key)
        if prev_meta.exists():
            try:
                prev = _json.loads(prev_meta.read_text(encoding="utf-8"))
                last = float(prev.get("_last_touched") or 0)
                if last and (time.time() - last) > idle_min * 60:
                    key += f"_cut{int(time.time() // 60)}"
            except Exception:
                pass

    return key
