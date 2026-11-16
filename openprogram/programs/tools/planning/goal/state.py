"""Goal meta read / write — the session's goal dict and the shared
stop-rule constants, plus the ``goal_update`` fan-out every state
change goes through."""
from __future__ import annotations

import logging
import math
import time
from copy import deepcopy
from typing import Callable, Optional

# The two refusals are defined by the usage ledger's Goal port, which raises
# them too; one class each keeps ``except`` clauses on either side matching.
from openprogram.models.usage.goal_port import (  # noqa: F401
    GoalBusy, GoalConflictError, GoalStateUnavailable, GoalVersionConflict,
)

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.tools.planning.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.tools.planning.goal as _goal

_log = logging.getLogger(__name__)

# Automatic turns in a row without a tool call before a Goal stops; the first is warned.
IDLE_ROUND_LIMIT = 2
_CLEAR_VERBS = {"clear", "off", "cancel"}
GOAL_SCHEMA_VERSION = 3
RUNNING_STATUSES = {"refining", "active", "running", "evaluating"}
WAITING_STATUSES = {"paused", "paused_recoverable", "waiting_user", "waiting_external"}
RESUMABLE_STATUSES = WAITING_STATUSES | {
    "blocked", "stalled", "budget_exhausted", "capped", "error", "failed",
}
TERMINAL_STATUSES = {"achieved", "impossible", "failed", "cancelled", "cleared"}


def positive_int(value, *, name: str) -> int | None:
    """A limit from user input; empty or zero means no limit."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if (not isinstance(value, str) and parsed != value) or parsed < 0:
        raise ValueError(f"{name} must be a positive integer or zero")
    return parsed or None


def positive_float(value, *, name: str) -> float | None:
    """A limit from user input; empty or zero means no limit."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a finite positive number or zero")
    return parsed or None


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
    from openprogram.runtime.context.session_db import default_db
    return default_db()


def load_goal(session_id: str) -> Optional[dict]:
    """The session's goal dict, or ``None``. Re-read fresh each time so a
    ``/goal clear`` from another surface takes effect on the next check.

    Every model request reads the Goal, so the read takes only
    ``meta.json`` from disk; dropping the session cache instead re-read
    every history node of a long conversation on each call."""
    try:
        db = _goal._db()
        read_disk_meta = getattr(db, "read_disk_meta", None)
        if callable(read_disk_meta):
            meta = read_disk_meta(session_id) or {}
            goal = meta.get("goal")
        else:
            invalidate = getattr(db, "invalidate_cache", None)
            if callable(invalidate):
                invalidate(session_id)
            meta = db.get_session(session_id) or {}
            goal = (meta.get("extra_meta") or {}).get("goal")
        if goal is None:
            return None
        if not isinstance(goal, dict):
            raise ValueError("Invalid persisted Goal")
        value = normalize_goal(goal)
        if not value.get("branch_id") and isinstance(meta, dict) and meta.get("active_branch_id"):
            # A Goal saved before Goals recorded their branch belongs to the
            # branch that was selected (Codex keeps a goal per thread):
            # turns on other branches neither advance nor settle it.
            value["branch_id"] = meta["active_branch_id"]
        if value.get("usage_mode") == "requests":
            # Read projection only: no lifecycle mutation or version increment.
            # Durable receipts are authoritative even if their publish hook failed.
            accumulate_goal_usage(session_id, value)
        from .presentation import history_projection
        value = history_projection(value, session_id)
        return value
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
    value["recoverable"] = value.get("status") in RESUMABLE_STATUSES
    usage = dict(value.get("usage") or {})
    usage.setdefault("active_elapsed_s", 0.0)
    if (value.get("execution_mode") == "chat" and value.get("pause_reason") == "worker_restart"
            and value.get("execution_id") and not value.get("usage_accounted_at")
            and value.get("accounted_execution_id") != value.get("execution_id")):
        usage["accounting_pending"] = True
        usage["cost_known"] = False
        usage["tokens_known"] = False
    value["usage"] = usage
    value.setdefault("budget", {})
    return value


def _chat_active_boundary(goal: dict, current: float) -> tuple[float, bool, bool]:
    """Return observed activity time, exactness and whether the owner is live.

    Owner-loss discovery is not the time a worker stopped. The last heartbeat
    provides a lower bound; unobserved time remains unknown, never offline time.
    """
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.attempts import AttemptStore, AttemptStatus
    started = float(goal["active_started_at"])
    try:
        store = default_store()
        execution = store.get_execution(goal.get("execution_id") or "")
        attempt_id = goal.get("active_attempt_id") or (execution.current_attempt_id if execution else None)
        attempts = AttemptStore(store)
        attempt = attempts.get(attempt_id) if attempt_id else None
        events = None
        if attempt is None and execution:
            events = store.list_events(execution.execution_id)
            ended = next((event for event in reversed(events) if event.kind == "attempt.ended"), None)
            if ended:
                attempt = attempts.get(ended.payload["attempt"]["attempt_id"])
        if attempt is None:
            return started, False, False
        if attempt.status is not AttemptStatus.ENDED:
            live = execution is not None and execution.current_attempt_id == attempt.attempt_id
            return (current, True, True) if live else (min(current, attempt.updated_at), False, False)
        if not attempt.fenced:
            return min(current, attempt.ended_at or started), True, False
        events = events if events is not None else store.list_events(attempt.execution_id)
        ended = next((event for event in reversed(events) if event.kind == "attempt.ended"
                      and event.payload.get("attempt", {}).get("attempt_id") == attempt.attempt_id), None)
        return min(current, float(ended.payload.get("last_active_at", started)) if ended else started), False, False
    except Exception:
        # Missing accounting evidence cannot become a fabricated wall duration.
        return started, False, False


def checkpoint_active_elapsed(
    goal: dict, *, now: float | None = None, stop: bool = False,
) -> float:
    """Accumulate active controller time without charging paused/waiting time."""
    current = time.time() if now is None else float(now)
    usage = dict(goal.get("usage") or {})
    elapsed = float(usage.get("active_elapsed_s") or 0.0)
    started = goal.get("active_started_at")
    live = False
    if started is not None:
        current, known, live = _chat_active_boundary(goal, current)
        if not known:
            usage["active_time_known"] = False
        elapsed += max(0.0, current - float(started))
    usage["active_elapsed_s"] = elapsed
    goal["usage"] = usage
    goal["active_started_at"] = None if stop or not live else current
    return elapsed


def _end_records(previous: dict, candidate: dict, anchor: str | None) -> list:
    """Terminal history travels with the current Goal, never inside itself."""
    records = deepcopy(previous.get("end_records") or [])
    for ended in (previous, candidate):
        if (ended.get("status") not in {"achieved", "impossible", "cancelled", "cleared"}
                or not ended.get("goal_id")):
            continue
        if any(row["goal"].get("goal_id") == ended["goal_id"] for row in records):
            continue
        snapshot = deepcopy({key: value for key, value in ended.items()
                             if key != "end_records"})
        verification = ended.get("verification") or {}
        verified_anchor = (verification.get("result_message_id")
                           if ended.get("status") == "achieved" else None)
        records.append({"anchor_id": verified_anchor or anchor,
                        "goal": snapshot})
    return records


def save_goal(session_id: str, goal: dict) -> dict:
    """Persist one complete Goal snapshot with optimistic concurrency."""
    expected = int(goal.get("version") or 0)
    candidate = dict(goal)
    candidate["recoverable"] = candidate.get("status") in RESUMABLE_STATUSES
    candidate["schema_version"] = GOAL_SCHEMA_VERSION
    candidate["version"] = expected + 1
    candidate["updated_at"] = time.time()
    db = _goal._db()
    session = db.get_session(session_id) or {}
    from .presentation import history_projection
    previous = history_projection((session.get("extra_meta") or {}).get("goal"), session_id) or {}
    # Keep terminal snapshots inside the same CAS as the current Goal. They
    # are not chat messages and must never be reconstructed from a later Goal.
    candidate["end_records"] = _end_records(previous, candidate, session.get("head_id"))
    compare = getattr(db, "compare_and_set_session_dict", None)
    if callable(compare):
        if not compare(session_id, "goal", version=expected, value=candidate):
            raise GoalVersionConflict(
                f"Goal version conflict: expected {expected}"
            )
    else:
        db.update_session(session_id, goal=candidate)
    goal.update(candidate)
    return goal


def goal_usage(session_id: str, since: float, until: float | None = None) -> dict:
    """Aggregate provider-recorded usage for this Goal's session window."""
    try:
        from openprogram.models.usage import default_ledger
        rows = default_ledger.query(
            since=since, until=until, filters={"session_id": session_id},
        )
        row = rows[0] if rows else None
        return {
            "total_tokens": int(row.total_tokens if row else 0),
            "available": True,
            "cost_usd": float(row.cost_total if row else 0.0),
            "cost_known": bool(row.cost_known if row else True),
            "unknown_cost_events": int(row.unknown_cost_events if row else 0),
        }
    except Exception:
        return {
            "total_tokens": 0,
            "available": False,
            "cost_usd": 0.0,
            "cost_known": False,
            "unknown_cost_events": 1,
        }


def reset_goal_usage_cursor(session_id: str, goal: dict) -> None:
    """Exclude all session usage that happened before this active run."""
    if goal.get("usage_mode") == "requests":
        _goal.accumulate_goal_usage(session_id, goal)
        if goal.get("usage_pending_until") is not None:
            raise GoalStateUnavailable("Goal usage ledger unavailable")
        return
    if goal.get("usage_pending_until") is not None:
        raise GoalStateUnavailable("Goal usage must be reconciled before starting another turn.")
    current = _goal.goal_usage(session_id, 0.0)
    if current.get("available") is False:
        raise GoalStateUnavailable("Usage ledger unavailable; Goal accounting cursor was preserved.")
    goal["usage_cursor"] = current


def accumulate_goal_usage(session_id: str, goal: dict, *, until: float | None = None) -> None:
    """Add only usage recorded since this Goal controller's last boundary."""
    if goal.get("usage_mode") == "requests":
        from openprogram.models.usage.request import project
        try:
            project(session_id, goal)
        except Exception:
            goal["usage"] = dict(goal.get("usage") or {}, cost_known=False, tokens_known=False, accounting_pending=True)
            goal["usage_pending_until"] = time.time()
        return
    current = (_goal.goal_usage(session_id, 0.0) if until is None
               else _goal.goal_usage(session_id, 0.0, until))
    if current.get("available") is False:
        goal.setdefault("usage_pending_known", {
            "cost_known": (goal.get("usage") or {}).get("cost_known", True),
            "tokens_known": (goal.get("usage") or {}).get("tokens_known", True),
        })
        goal["usage"] = dict(goal.get("usage") or {}, cost_known=False, tokens_known=False)
        goal["usage_pending_until"] = time.time() if until is None else until
        return
    cursor = dict(goal.get("usage_cursor") or current)
    usage = dict(goal.get("usage") or {})
    usage.update(goal.pop("usage_pending_known", {}))
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
    goal["usage_accounted_at"] = time.time()
    usage.pop("accounting_pending", None)
    goal.pop("usage_pending_until", None)


def budget_exhausted(goal: dict, *, now: float | None = None) -> str:
    """Return the first exhausted Goal budget, or an empty string."""
    budget = goal.get("budget") or {}
    usage = goal.get("usage") or {}
    if budget.get("max_tokens") and int(usage.get("total_tokens") or 0) >= int(budget["max_tokens"]):
        return "tokens"
    if budget.get("max_elapsed_s"):
        observed = dict(goal, usage=dict(usage))
        elapsed = checkpoint_active_elapsed(observed, now=now)
        if observed["usage"].get("active_time_known") is False:
            return "elapsed_time_unknown"
        if elapsed >= float(budget["max_elapsed_s"]):
            return "elapsed_time"
    if budget.get("max_cost_usd") is not None and usage.get("cost_known"):
        if float(usage.get("cost_usd") or 0.0) >= float(budget["max_cost_usd"]):
            return "cost"
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
            "schema_version", "execution_mode", "goal_id", "run_id", "revision", "version",
            "text", "checklist", "progress", "status", "phase", "budget", "usage", "execution_id",
            "recoverable", "pause_reason", "stop_requested", "last_reason",
            "created_at", "updated_at", "end_records")},
    }
    if payload["goal"]["progress"] is None:
        # A Goal without a task has no progress to show.
        del payload["goal"]["progress"]
    from .presentation import goal_message
    payload["goal"]["verification_message"] = goal_message(goal)
    if on_event is not None:
        try:
            on_event({"type": "chat_response", "data": dict(payload)})
        except Exception:
            _log.debug("goal on_event emit failed", exc_info=True)
    # 事件层 tap：goal 状态变化进总线（emit_safe 自己吞异常）。
    from openprogram.runtime.events import emit_safe
    emit_safe("goal.update", "system",
              {"session_id": session_id, "goal": dict(payload["goal"])},
              {"session": session_id})
    try:
        from openprogram.runtime import interface_link
        interface_link.broadcast({"type": "goal_update", "data": payload})
    except Exception:
        # No interface may be running (CLI, tests).
        _log.debug("goal update broadcast failed session=%s", session_id, exc_info=True)
