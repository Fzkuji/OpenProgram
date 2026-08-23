"""Goal meta read / write — the session's goal dict and the shared
stop-rule constants, plus the ``goal_update`` fan-out every state
change goes through."""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.workflow.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.workflow.goal as _goal

_log = logging.getLogger(__name__)

JUDGE_PARSE_FAILURE_LIMIT = 3
# 连续 N 个续轮判定打勾数不涨 → 无进展停机(只读磨洋工守卫)。
STALL_ROUND_LIMIT = 3
# 未配置时的默认轮数上限（对齐 OpenHands 的 500 量级预算 + Codex goal
# 模式 200，取更保守的 150；显式配 0/负数才表示无限）。
DEFAULT_MAX_TURNS = 150
# 连续 N 轮零工具且仍 unmet → 判定为 idle spin 停机；第 1 次先警告。
IDLE_ROUND_LIMIT = 2
_CLEAR_VERBS = {"clear", "stop", "off", "cancel"}


def _db():
    from openprogram.agent.session_db import default_db
    return default_db()


def load_goal(session_id: str) -> Optional[dict]:
    """The session's goal dict, or ``None``. Re-read fresh each time so a
    ``/goal clear`` from another surface takes effect on the next check."""
    try:
        db = _goal._db()
        invalidate = getattr(db, "invalidate_cache", None)
        if callable(invalidate):
            invalidate(session_id)
        sess = db.get_session(session_id) or {}
        goal = (sess.get("extra_meta") or {}).get("goal")
        return dict(goal) if isinstance(goal, dict) else None
    except Exception:
        _log.debug("goal read failed for session %s", session_id, exc_info=True)
        return None


def save_goal(session_id: str, goal: dict) -> None:
    _goal._db().update_session(session_id, goal=dict(goal))


def default_max_turns() -> Optional[int]:
    """``goal.max_turns`` from config.json (config_schema setting).
    Unset — the default — means :data:`DEFAULT_MAX_TURNS` (150), the
    runaway budget every Goal run starts with. An explicit zero or
    negative value means NO turn cap; an explicit positive value is
    honoured as-is."""
    try:
        from openprogram import setup as _setup
        v = (_setup._read_config().get("goal") or {}).get("max_turns")
        if v in (None, ""):
            return DEFAULT_MAX_TURNS
        n = int(v)
        return n if n > 0 else None
    except Exception:
        return DEFAULT_MAX_TURNS


def _emit_goal_update(on_event: Optional[Callable], session_id: str,
                      goal: dict) -> None:
    """Fan the goal state out: dispatcher event stream (for the calling
    surface) + webui broadcast (all connected tabs; best-effort — the
    server may not be running, e.g. pure-CLI use or tests)."""
    payload = {
        "type": "goal_update",
        "session_id": session_id,
        "goal": {k: goal.get(k) for k in (
            "text", "spec", "checklist", "status", "turns_used",
            "max_turns", "last_reason", "last_question",
            "last_question_at", "last_question_options")},
    }
    if on_event is not None:
        try:
            on_event({"type": "chat_response", "data": dict(payload)})
        except Exception:
            _log.debug("goal on_event emit failed", exc_info=True)
    # 事件层 tap：goal 状态变化进总线（emit_safe 自己吞异常）。
    from openprogram.events import emit_safe
    emit_safe("goal.update", "system",
              {"session_id": session_id, "goal": dict(payload["goal"])},
              {"session": session_id})
    try:
        from openprogram.webui import server as _s
        _s._broadcast(json.dumps({"type": "goal_update", "data": payload},
                                 default=str))
    except Exception:
        pass
