"""Goal meta read / write — the session's goal dict and the shared
stop-rule constants, plus the ``goal_update`` fan-out every state
change goes through."""
from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
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
DEFAULT_PHASE_TIMEOUT_S = 300.0
# 连续 N 轮零工具且仍 unmet → 判定为 idle spin 停机；第 1 次先警告。
IDLE_ROUND_LIMIT = 2
_CLEAR_VERBS = {"clear", "stop", "off", "cancel"}
GOAL_SCHEMA_VERSION = 3
RUNNING_STATUSES = {"refining", "active", "running", "evaluating"}
WAITING_STATUSES = {"paused", "paused_recoverable", "waiting_user", "waiting_external"}
RESUMABLE_STATUSES = WAITING_STATUSES | {
    "blocked", "stalled", "budget_exhausted", "capped", "error", "failed",
}
TERMINAL_STATUSES = {"achieved", "impossible", "failed", "cancelled", "cleared"}


class GoalConflictError(ValueError):
    """A stale controller attempted to replace a newer Goal snapshot."""


class GoalStateUnavailable(RuntimeError):
    """Persistent Goal state could not be read or committed safely."""


def check_goal_preconditions(goal: dict, expected: dict | None) -> None:
    """Reject an action aimed at a different saved Goal or revision."""
    if expected is None:
        return
    fields = {"goal_id", "revision", "run_id", "version"}
    if not isinstance(expected, dict) or not expected or expected.keys() - fields:
        raise ValueError("Invalid Goal preconditions")
    if any(goal.get(key) != value for key, value in expected.items()):
        raise GoalConflictError("Goal changed; reload its details before retrying")


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
        if goal is None:
            return None
        if not isinstance(goal, dict):
            raise ValueError("Invalid persisted Goal")
        return normalize_goal(goal)
    except Exception as exc:
        _log.debug("goal read failed for session %s", session_id, exc_info=True)
        raise GoalStateUnavailable("Goal state unavailable; retry after storage recovers.") from exc


def normalize_goal(goal: dict) -> dict:
    """Read old Goal dictionaries through the current durable schema."""
    value = dict(goal)
    try:
        stored_schema = int(value.get("schema_version") or 0)
    except (TypeError, ValueError):
        stored_schema = 0
    value["schema_version"] = max(GOAL_SCHEMA_VERSION, stored_schema)
    value.setdefault("goal_id", "")
    value.setdefault("run_id", "")
    value.setdefault("revision", 1)
    value.setdefault("version", 0)
    value.setdefault("turns_used", 0)
    value["recoverable"] = value.get("status") in RESUMABLE_STATUSES
    usage = dict(value.get("usage") or {})
    usage.setdefault("active_elapsed_s", 0.0)
    value["usage"] = usage
    value.setdefault("budget", {"max_turns": value.get("max_turns")})
    value.setdefault("checkpoint", {})
    questions = []
    for index, item in enumerate(value.get("questions") or [], 1):
        if not isinstance(item, dict):
            continue
        question = dict(item)
        question.setdefault("id", f"legacy-question-{index}")
        question.setdefault("status", "pending")
        question["options"] = [
            {"label": option.strip(), "description": ""}
            if isinstance(option, str) else {
                "label": str(option.get("label") or "").strip(),
                "description": str(option.get("description") or ""),
            }
            for option in (question.get("options") or [])
            if (isinstance(option, str) and option.strip())
            or (isinstance(option, dict) and str(option.get("label") or "").strip())
        ]
        questions.append(question)
    if value.get("last_question") and not questions:
        legacy_options = [
            {"label": option.strip(), "description": ""}
            if isinstance(option, str) else {
                "label": str(option.get("label") or "").strip(),
                "description": str(option.get("description") or ""),
            }
            for option in (value.get("last_question_options") or [])
            if (isinstance(option, str) and option.strip())
            or (isinstance(option, dict) and str(option.get("label") or "").strip())
        ]
        questions.append({
            "id": str(value.get("last_question_id") or "legacy-question"),
            "prompt": str(value.get("last_question") or ""),
            "options": legacy_options,
            "reason": str(value.get("last_reason") or ""),
            "status": "pending",
            "asked_at": float(value.get("last_question_at") or 0),
            "can_continue": False,
        })
    value["questions"] = questions
    value.setdefault("pending_answers", [])
    return value


def checkpoint_active_elapsed(
    goal: dict, *, now: float | None = None, stop: bool = False,
) -> float:
    """Accumulate active controller time without charging paused/waiting time."""
    current = time.time() if now is None else float(now)
    usage = dict(goal.get("usage") or {})
    elapsed = float(usage.get("active_elapsed_s") or 0.0)
    started = goal.get("active_started_at")
    if started is not None:
        elapsed += max(0.0, current - float(started))
    usage["active_elapsed_s"] = elapsed
    goal["usage"] = usage
    goal["active_started_at"] = None if stop else current
    return elapsed


def save_goal(session_id: str, goal: dict) -> dict:
    """Persist one complete Goal snapshot with optimistic concurrency."""
    expected = int(goal.get("version") or 0)
    candidate = dict(goal)
    candidate["recoverable"] = candidate.get("status") in RESUMABLE_STATUSES
    candidate["schema_version"] = GOAL_SCHEMA_VERSION
    candidate["version"] = expected + 1
    candidate["updated_at"] = time.time()
    db = _goal._db()
    compare = getattr(db, "compare_and_set_session_dict", None)
    if callable(compare):
        if not compare(session_id, "goal", version=expected, value=candidate):
            raise GoalConflictError(
                f"Goal version conflict: expected {expected}"
            )
    else:
        db.update_session(session_id, goal=candidate)
    goal.update(candidate)
    return goal


def _merge_progress(base: dict, local: dict, latest: dict) -> dict:
    """Preserve controller progress and concurrent actions from the same run."""
    if (
        not local.get("run_id")
        or any(latest.get(key) != local.get(key) for key in ("goal_id", "run_id"))
        or latest.get("status") not in RUNNING_STATUSES
    ):
        raise GoalConflictError("Goal controller no longer owns this run")
    merged = deepcopy(local)
    missing = object()
    for key in base.keys() | latest.keys():
        if base.get(key, missing) != latest.get(key, missing):
            if key in latest:
                merged[key] = deepcopy(latest[key])
            else:
                merged.pop(key, None)
    # Merge queues by identity: remote answers win, but local additions and
    # removal of already-consumed answers must survive unrelated actions.
    for key, identity in (("questions", "id"), ("pending_answers", "question_id")):
        def indexed(state):
            return {
                str(item.get(identity) or json.dumps(item, sort_keys=True)): item
                for item in (state.get(key) or []) if isinstance(item, dict)
            }
        before, ours, theirs = indexed(base), indexed(local), indexed(latest)
        result = deepcopy(ours)
        for item_id in dict.fromkeys([*before, *theirs]):
            if before.get(item_id, missing) != theirs.get(item_id, missing):
                if item_id in theirs:
                    result[item_id] = deepcopy(theirs[item_id])
                else:
                    result.pop(item_id, None)
        merged[key] = list(result.values())
    if merged.get("pending_answers") and local.get("status") not in RUNNING_STATUSES:
        merged.update({
            "status": "paused_recoverable", "phase": "answer_received",
            "pause_reason": "answer_received",
            "last_reason": "An answer arrived during the stop decision; resume to apply it.",
            "active_started_at": None,
        })
        merged["checkpoint"] = {
            **(merged.get("checkpoint") or {}), "phase": "answer_received",
        }
    merged["version"] = int(latest.get("version") or 0) + 1
    merged["schema_version"] = GOAL_SCHEMA_VERSION
    merged["updated_at"] = time.time()
    merged["recoverable"] = merged.get("status") in RESUMABLE_STATUSES
    return merged


def save_goal_progress(session_id: str, goal: dict, base: dict) -> dict:
    """CAS first, then merge a same-run action atomically without retry loops."""
    try:
        return _goal.save_goal(session_id, goal)
    except GoalConflictError:
        committed = _goal._db().update_session_dict(
            session_id, "goal", lambda latest: _merge_progress(base, goal, latest),
        )
        if committed is None:
            raise GoalConflictError("Goal session is unavailable")
        goal.clear()
        goal.update(committed)
        return goal


def goal_usage(session_id: str, since: float) -> dict:
    """Aggregate provider-recorded usage for this Goal's session window."""
    try:
        from openprogram.usage import default_ledger
        rows = default_ledger.query(
            since=since, filters={"session_id": session_id},
        )
        row = rows[0] if rows else None
        return {
            "total_tokens": int(row.total_tokens if row else 0),
            "cost_usd": float(row.cost_total if row else 0.0),
            "cost_known": bool(row.cost_known if row else True),
            "unknown_cost_events": int(row.unknown_cost_events if row else 0),
        }
    except Exception:
        return {
            "total_tokens": 0,
            "cost_usd": 0.0,
            "cost_known": False,
            "unknown_cost_events": 1,
        }


def reset_goal_usage_cursor(session_id: str, goal: dict) -> None:
    """Exclude all session usage that happened before this active run."""
    goal["usage_cursor"] = _goal.goal_usage(session_id, 0.0)


def accumulate_goal_usage(session_id: str, goal: dict) -> None:
    """Add only usage recorded since this Goal controller's last boundary."""
    current = _goal.goal_usage(session_id, 0.0)
    cursor = dict(goal.get("usage_cursor") or current)
    usage = dict(goal.get("usage") or {})
    token_delta = max(
        0,
        int(current.get("total_tokens") or 0)
        - int(cursor.get("total_tokens") or 0),
    )
    cost_delta = max(
        0.0,
        float(current.get("cost_usd") or 0.0)
        - float(cursor.get("cost_usd") or 0.0),
    )
    unknown_delta = max(
        0,
        int(current.get("unknown_cost_events") or 0)
        - int(cursor.get("unknown_cost_events") or 0),
    )
    usage["total_tokens"] = int(usage.get("total_tokens") or 0) + token_delta
    usage["cost_usd"] = float(usage.get("cost_usd") or 0.0) + cost_delta
    usage["cost_known"] = bool(usage.get("cost_known", True)) and unknown_delta == 0
    goal["usage"] = usage
    goal["usage_cursor"] = current


def budget_exhausted(goal: dict, *, now: float | None = None) -> str:
    """Return the first exhausted Goal budget, or an empty string."""
    budget = goal.get("budget") or {}
    usage = goal.get("usage") or {}
    if budget.get("max_turns") and int(goal.get("turns_used") or 0) >= int(budget["max_turns"]):
        return "turns"
    if budget.get("max_tokens") and int(usage.get("total_tokens") or 0) >= int(budget["max_tokens"]):
        return "tokens"
    if budget.get("max_elapsed_s"):
        elapsed = float(usage.get("active_elapsed_s") or 0.0)
        if goal.get("active_started_at") is not None:
            elapsed += max(
                0.0,
                (time.time() if now is None else float(now))
                - float(goal["active_started_at"]),
            )
        if elapsed >= float(budget["max_elapsed_s"]):
            return "elapsed_time"
    if budget.get("max_cost_usd") is not None and usage.get("cost_known"):
        if float(usage.get("cost_usd") or 0.0) >= float(budget["max_cost_usd"]):
            return "cost"
    return ""


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


def judge_model() -> str:
    """``goal.judge_model`` from config.json — the model override the
    completion judge runs on. Empty means the session's picked model."""
    try:
        from openprogram import setup as _setup
        return str((_setup._read_config().get("goal") or {})
                   .get("judge_model") or "").strip()
    except Exception:
        return ""


def _emit_goal_update(on_event: Optional[Callable], session_id: str,
                      goal: dict) -> None:
    """Fan the goal state out: dispatcher event stream (for the calling
    surface) + webui broadcast (all connected tabs; best-effort — the
    server may not be running, e.g. pure-CLI use or tests)."""
    payload = {
        "type": "goal_update",
        "session_id": session_id,
        "goal": {k: goal.get(k) for k in (
            "schema_version", "goal_id", "run_id", "revision", "version",
            "text", "spec", "checklist", "status", "phase", "turns_used",
            "max_turns", "budget", "usage", "checkpoint", "execution_id",
            "recoverable", "pause_reason", "last_reason", "last_question",
            "last_question_id", "last_question_at", "last_question_options",
            "questions", "pending_answers", "interaction_mode", "created_at",
            "updated_at", "roles", "roles_origin", "role_requests")},
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
