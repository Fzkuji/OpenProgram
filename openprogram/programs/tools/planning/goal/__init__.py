"""Persistent chat Goals.

Interactive ``/goal`` and Goal controls save objective state and execute normal
chat turns. ``chat`` integrates admission, terminal accounting and continuation;
``chat_tools`` exposes short create/get/update operations. Existing todo items
can carry the Goal identity and revision. Completion candidates are checked by
an independent, read-only ordinary chat turn in ``verification``.

* ``goal``: the Programs form entry that starts a chat Goal
* ``command``: ``/goal`` parsing plus status and mutation actions
* ``state``: goal meta read / write, budgets, event fan-out
* ``execution``: canonical execution state and action eligibility
* ``notices``: transcript system rows
* ``ownership``: the session's cross-process Goal owner lock
* ``restart``: the recoverable pause of a Workflow Goal after a worker restart

Design doc: docs/reference/design/runtime/reliability/goal-and-recovery.html.

Goal state is one versioned session snapshot: the objective, typed lifecycle
status, optional token / active-time / cost limits and cumulative usage.
``goal_id`` is stable across resume; each resume starts a new ``run_id``.

Tests monkeypatch functions ON THIS PACKAGE (``monkeypatch.setattr(G,
"load_goal", ...)`` with ``G = openprogram.programs.tools.planning.goal``); the
submodules therefore call each other through the package object, and
these re-exports are the single authoritative binding every internal
call site resolves against.
"""
from __future__ import annotations

from . import chat_tools as _chat_tools  # noqa: F401

from openprogram.programs.tools.planning.goal.goal import ENTRY_OPTIONS, PROGRAM_ENTRIES, goal  # noqa: F401
from openprogram.programs.tools.planning.goal.state import (  # noqa: F401
    GOAL_SCHEMA_VERSION,
    GoalBusy,
    GoalConflictError,
    GoalStateUnavailable,
    GoalVersionConflict,
    IDLE_ROUND_LIMIT,
    RESUMABLE_STATUSES,
    RUNNING_STATUSES,
    TERMINAL_STATUSES,
    WAITING_STATUSES,
    _CLEAR_VERBS,
    _db,
    _emit_goal_update,
    accumulate_goal_usage,
    budget_exhausted,
    checkpoint_active_elapsed,
    check_goal_preconditions,
    goal_usage,
    load_goal,
    normalize_goal,
    positive_float,
    positive_int,
    reset_goal_usage_cursor,
    save_goal,
)
from openprogram.programs.tools.planning.goal.notices import (  # noqa: F401
    _TERMINAL_LABELS,
    _emit_goal_notice,
)
from openprogram.programs.tools.planning.goal.execution import (  # noqa: F401
    GoalStopUnconfirmed,
    goal_control_state,
    goal_execution_state,
    goal_projection,
    request_goal_stop,
    require_goal_execution_finished,
)
from openprogram.programs.tools.planning.goal.command import (  # noqa: F401
    _status_text,
    apply_goal_action,
    goal_builtin_handler,
    handle_goal_command,
)
