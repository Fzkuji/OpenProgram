"""Reusable Goal Workflow and the persistent ``/goal`` session integration.

``/goal <condition>`` stores a goal in the session meta. After every
completed turn the dispatcher asks :func:`continue_goal_turns` whether
the goal is met; while it is not (and no stop rule fires) the loop
launches a follow-up turn (``source="goal_continue"``). Every
continuation turn is persisted, committed and compacted like any
user-sent turn.

Evaluation is one decision agent turn: :func:`judge_goal` (prompt in
its docstring) reads the session's compacted context view plus the
goal text and answers strict JSON
``{"met", "reason", "need_user", "question"}``. Only its "met" counts
as completion. ``goal()`` is the public Workflow entry that runs
agent work and reuses this judge. Session ``/goal`` is the same
Workflow adapted onto the chat turn lifecycle. Deterministic control
flow — retry accounting, stop rules, budgets, state writes — is split as

* ``goal``: public :func:`goal` entry
* ``command``: ``/goal`` set / clear / status
* ``judge``: :func:`judge_goal` and :func:`evaluate_goal`
* ``refinement``: background spec refinement after /goal set
* ``loop``: :func:`continue_goal_turns` and its stop rules
* ``state``: goal meta read / write, stop-rule constants, event fan-out
* ``notices``: transcript system rows, terminal finisher

The judge is separate from the working model on purpose: agents that
self-report completion (Codex / Cline style) systematically declare
victory early, so the verdict must come from outside the working
context. Design doc: docs/reference/design/runtime/goal.md.

Goal meta shape (``session extra_meta["goal"]``)::

    {"text": str, "spec": str (refined specification; absent until the
     refinement step lands, judging falls back to text),
     "checklist": [{"text": str, "done": bool}, …] (refinement-fixed
     acceptance items; absent when refinement produced none — the
     judge only flips "done", never edits the list),
     "status": "active" | "waiting_user" | "achieved" |
     "cleared" | "capped" | "error", "created_at": float,
     "turns_used": int, "max_turns": int | None (None = unlimited),
     "last_reason": str, "last_question": str,
     "last_question_at": float, "judge_parse_failures": int}

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
    JUDGE_PARSE_FAILURE_LIMIT,
    QUESTION_MIN_INTERVAL_SECONDS,
    STALL_ROUND_LIMIT,
    _CLEAR_VERBS,
    _db,
    _emit_goal_update,
    default_max_turns,
    load_goal,
    save_goal,
)
from openprogram.programs.workflow.goal.notices import (  # noqa: F401
    _TERMINAL_LABELS,
    _emit_goal_notice,
    _emit_goal_question,
    _finish,
)
from openprogram.programs.workflow.goal.refinement import (  # noqa: F401
    REFINE_TOOLS,
    _adopt_refinement,
    _emit_goal_spec_notice,
    _parse_refinement,
    _run_refine_turn,
    _start_spec_refinement,
    refine_goal_spec,
    refine_goal_spec_candidate,
)
from openprogram.programs.workflow.goal.loop import (  # noqa: F401
    _inherit_parent,
    _tools_with_forced_web_search,
    apply_callable_verdict,
    continue_goal_turns,
)
from openprogram.programs.workflow.goal.command import (  # noqa: F401
    _status_text,
    goal_builtin_handler,
    handle_goal_command,
)
