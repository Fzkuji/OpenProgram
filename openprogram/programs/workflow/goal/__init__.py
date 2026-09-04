"""The single Goal Workflow and its ``/goal`` command adapter.

Programs and Python call :func:`goal` with isolated pre-call context.
``/goal <prompt>`` invokes the same function with the current session
view as initial evidence. The function owns refinement, work rounds,
judgment, the asynchronous question queue, state writes and terminal behavior.

Evaluation is one decision agent turn: :func:`judge_goal` (prompt in
its docstring) reads the session's compacted context view plus the
goal text and answers strict JSON with a typed verdict, reason, optional
question, independent-work flag, options, and checklist. Only its ``met`` counts
as completion. Deterministic responsibilities are split as

* ``goal``: public :func:`goal` entry
* ``command``: ``/goal`` parsing plus status and mutation actions
* ``judge``: :func:`judge_goal` and :func:`evaluate_goal`
* ``refinement``: the one refinement operation used by :func:`goal`
* ``loop``: deterministic transitions and next-round instructions
* ``state``: goal meta read / write, stop-rule constants, event fan-out
* ``notices``: transcript system rows, terminal finisher

The judge is separate from the working model on purpose: agents that
self-report completion (Codex / Cline style) systematically declare
victory early, so the verdict must come from outside the working
context. Design doc:
docs/reference/design/runtime/goal-framework-implementation-comparison.html.

Goal state is one versioned session snapshot. It contains the objective and
refined checklist, typed lifecycle status, controller checkpoint, cumulative
budgets and usage, an ordered question queue, and answers waiting for the next
controller boundary. ``goal_id`` is stable across resume; each execution has a
new ``run_id``. See the design document for the complete schema and states.

Tests monkeypatch functions ON THIS PACKAGE (``monkeypatch.setattr(G,
"evaluate_goal", ...)`` with ``G = openprogram.programs.workflow.goal``); the
submodules therefore call each other through the package object, and
these re-exports are the single authoritative binding every internal
call site resolves against.
"""
from __future__ import annotations

from openprogram.programs.workflow.goal.goal import goal  # noqa: F401
from openprogram.programs.workflow.goal.judge import (  # noqa: F401
    DECISION_TOOLS,
    _parse_decision,
    _run_decision_turn,
    evaluate_goal,
    judge_goal,
    render_session_view,
)
from openprogram.programs.workflow.goal.state import (  # noqa: F401
    DEFAULT_MAX_TURNS,
    DEFAULT_PHASE_TIMEOUT_S,
    GOAL_SCHEMA_VERSION,
    GoalConflictError,
    GoalStateUnavailable,
    IDLE_ROUND_LIMIT,
    JUDGE_PARSE_FAILURE_LIMIT,
    RESUMABLE_STATUSES,
    RUNNING_STATUSES,
    STALL_ROUND_LIMIT,
    TERMINAL_STATUSES,
    WAITING_STATUSES,
    _CLEAR_VERBS,
    _db,
    _emit_goal_update,
    accumulate_goal_usage,
    budget_exhausted,
    checkpoint_active_elapsed,
    check_goal_preconditions,
    default_max_turns,
    goal_usage,
    judge_model,
    load_goal,
    normalize_goal,
    reset_goal_usage_cursor,
    save_goal,
    save_goal_progress,
)
from openprogram.programs.workflow.goal.notices import (  # noqa: F401
    _TERMINAL_LABELS,
    _emit_goal_notice,
    _finish,
)
from openprogram.programs.workflow.goal.execution import (  # noqa: F401
    GoalStopUnconfirmed,
    goal_execution_state,
    request_goal_stop,
    require_goal_execution_finished,
)
from openprogram.programs.workflow.goal.refinement import (  # noqa: F401
    REFINE_TOOLS,
    _parse_refinement,
    _run_refine_turn,
    refine_goal_spec_candidate,
)
from openprogram.programs.workflow.goal.loop import (  # noqa: F401
    IDLE_WARNING,
    apply_checklist_stall,
    apply_goal_verdict,
    apply_idle_spin,
    next_work_prompt,
)
from openprogram.programs.workflow.goal.command import (  # noqa: F401
    _resume_invocation,
    _status_text,
    apply_goal_action,
    goal_builtin_handler,
    handle_goal_command,
)
