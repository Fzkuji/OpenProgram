"""Behavior of the single public Goal Workflow and its command adapter."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import openprogram.programs.workflow.goal as G
from openprogram.agent.session_db import SessionDB


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    store = SessionDB(tmp_path / "sessions-git")
    store.create_session("s1", "main")
    monkeypatch.setattr(G, "_db", lambda: store)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    return store


class _Runtime:
    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = iter(answers or [])
        self.questions: list[tuple[str, object]] = []

    def ask(self, prompt, *, options=None, **_kwargs):
        self.questions.append((prompt, options))
        return next(self.answers, "")


def _run_goal(
    monkeypatch: pytest.MonkeyPatch,
    db: SessionDB,
    *,
    agent_outputs: list[str],
    verdicts: list[tuple[str, str, str, list]],
    max_rounds: int = 10,
    checklist: list[str] | None = None,
    runtime: _Runtime | None = None,
    context_mode: str = "isolated",
) -> tuple[str, list[str]]:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "goal-call")
    monkeypatch.setattr(
        G,
        "refine_goal_spec_candidate",
        lambda *_args, **_kwargs: (
            "SPEC",
            list(checklist or []),
        ),
    )
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(G, "_emit_goal_notice", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(G, "render_session_view", lambda _sid: "SESSION VIEW")

    outputs = iter(agent_outputs)
    prompts: list[str] = []

    def fake_agent(**kwargs):
        prompts.append(kwargs["prompt"])
        return next(outputs)

    decisions = iter(verdicts)
    monkeypatch.setattr(agent_module, "agent", fake_agent)
    monkeypatch.setattr(
        G,
        "evaluate_goal",
        lambda *_args, **_kwargs: next(decisions),
    )
    result = module.goal(
        "do work",
        "done",
        max_rounds=max_rounds,
        context_mode=context_mode,
        runtime=runtime or _Runtime(),
    )
    return result, prompts


def test_command_set_status_and_clear_use_the_workflow_state(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = G.handle_goal_command("s1", "tests pass")
    assert invocation["invoke"]["name"] == "goal"
    assert G.load_goal("s1") is None

    cancelled: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "openprogram.agent.run_control.mark_cancelled",
        lambda sid, *, execution_id=None: cancelled.append((sid, execution_id)),
    )
    G.save_goal("s1", {
        "text": "tests pass",
        "status": "active",
        "execution_id": "goal-call",
        "turns_used": 2,
        "max_turns": 10,
        "last_reason": "not yet",
    })
    status = G.handle_goal_command("s1", "")
    assert "Goal [active]: tests pass" in status["text"]
    assert "turns: 2/10" in status["text"]

    cleared = G.handle_goal_command("s1", "clear")
    assert cleared["send_text"] is None
    assert G.load_goal("s1")["status"] == "cleared"
    assert cancelled == [("s1", "goal-call")]


def test_goal_runs_one_loop_until_achieved(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["first", "finished"],
        verdicts=[
            ("unmet", "not yet", "", []),
            ("met", "done", "", []),
        ],
    )
    assert result == "finished"
    assert len(prompts) == 2
    assert "[goal] 未达成：not yet" in prompts[1]
    stored = G.load_goal("s1")
    assert stored["status"] == "achieved"
    assert stored["turns_used"] == 2


def test_goal_cap_and_judge_failure_use_the_same_transition_helpers(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_goal(
        monkeypatch,
        db,
        agent_outputs=["one"],
        verdicts=[("unmet", "not done", "", [])],
        max_rounds=1,
    )
    assert G.load_goal("s1")["status"] == "capped"

    _run_goal(
        monkeypatch,
        db,
        agent_outputs=["one"],
        verdicts=[("judge_failure", "bad", "", [])],
        max_rounds=1,
    )
    assert G.load_goal("s1")["status"] == "capped"

    _run_goal(
        monkeypatch,
        db,
        agent_outputs=["one", "two", "three"],
        verdicts=[
            ("judge_failure", "bad", "", []),
            ("judge_failure", "bad", "", []),
            ("judge_failure", "bad", "", []),
        ],
    )
    stored = G.load_goal("s1")
    assert stored["status"] == "error"
    assert stored["judge_parse_failures"] == G.JUDGE_PARSE_FAILURE_LIMIT


def test_nonpositive_round_limit_has_no_numeric_cap(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["one", "two", "three"],
        verdicts=[
            ("unmet", "no", "", []),
            ("unmet", "no", "", []),
            ("met", "done", "", []),
        ],
        max_rounds=0,
    )
    assert result == "three"
    assert len(prompts) == 3
    assert G.load_goal("s1")["status"] == "achieved"


def test_goal_asks_and_resumes_inside_the_same_workflow(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(["方案 A"])
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input", "finished"],
        verdicts=[
            (
                "needs_user",
                "direction required",
                "A 还是 B？",
                [{"label": "A", "description": "first"}],
            ),
            ("met", "done", "", []),
        ],
        runtime=runtime,
    )
    assert result == "finished"
    assert runtime.questions[0][0] == "A 还是 B？"
    assert "方案 A" in prompts[1]
    stored = G.load_goal("s1")
    assert stored["status"] == "achieved"
    assert "last_question" not in stored


def test_unanswered_goal_question_finishes_as_error(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="requires a user answer"):
        _run_goal(
            monkeypatch,
            db,
            agent_outputs=["need input"],
            verdicts=[(
                "needs_user",
                "direction required",
                "A or B?",
                [],
            )],
            runtime=_Runtime(),
        )
    stored = G.load_goal("s1")
    assert stored["status"] == "error"
    assert stored["last_reason"] == "Goal requires a user answer."


def test_answer_on_last_round_finishes_as_capped(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input"],
        verdicts=[("needs_user", "choose", "A or B?", [])],
        max_rounds=1,
        runtime=_Runtime(["A"]),
    )
    assert result == "need input"
    assert len(prompts) == 1
    assert G.load_goal("s1")["status"] == "capped"


def test_goal_clear_during_work_does_not_get_overwritten(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "goal-call")
    monkeypatch.setattr(
        G, "refine_goal_spec_candidate", lambda *_args, **_kwargs: ("SPEC", []),
    )
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_args, **_kwargs: None)

    def clear_then_return(**_kwargs):
        G.handle_goal_command("s1", "clear")
        return "stopped"

    monkeypatch.setattr(agent_module, "agent", clear_then_return)
    monkeypatch.setattr(
        G,
        "evaluate_goal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a cleared goal must not be judged")
        ),
    )
    assert module.goal(
        "do work", "done", runtime=_Runtime(),
    ) == "stopped"
    assert G.load_goal("s1")["status"] == "cleared"


def test_goal_clear_during_judge_does_not_get_overwritten(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "goal-call")
    monkeypatch.setattr(
        G, "refine_goal_spec_candidate", lambda *_args, **_kwargs: ("SPEC", []),
    )
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "openprogram.agent.run_control.mark_cancelled",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(agent_module, "agent", lambda **_kwargs: "finished")

    def clear_then_accept(*_args, **_kwargs):
        assert G.handle_goal_command("s1", "clear")["text"] == "Goal cleared."
        return "met", "done", "", []

    monkeypatch.setattr(G, "evaluate_goal", clear_then_accept)
    assert module.goal("do work", "done", runtime=_Runtime()) == "finished"
    assert G.load_goal("s1")["status"] == "cleared"


def test_goal_is_active_and_clearable_during_refinement(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "goal-call")
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "openprogram.agent.run_control.mark_cancelled",
        lambda *_args, **_kwargs: None,
    )

    def clear_during_refinement(*_args, **_kwargs):
        assert G.load_goal("s1")["status"] == "active"
        assert G.handle_goal_command("s1", "clear")["text"] == "Goal cleared."
        return "SPEC", ["item"]

    monkeypatch.setattr(G, "refine_goal_spec_candidate", clear_during_refinement)
    monkeypatch.setattr(
        agent_module,
        "agent",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("a cleared refinement must not start work")
        ),
    )

    assert module.goal("do work", "done", runtime=_Runtime()) == ""
    assert G.load_goal("s1")["status"] == "cleared"


def test_goal_cancellation_finishes_shared_state(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "goal-call")
    monkeypatch.setattr(
        G, "refine_goal_spec_candidate", lambda *_args, **_kwargs: ("SPEC", []),
    )
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(G, "_emit_goal_notice", lambda *_args, **_kwargs: None)

    def cancel(**_kwargs):
        raise function_module.CancelledError("stop")

    monkeypatch.setattr(agent_module, "agent", cancel)
    with pytest.raises(function_module.CancelledError):
        module.goal("do work", "done", runtime=_Runtime())
    stored = G.load_goal("s1")
    assert stored["status"] == "error"
    assert stored["last_reason"] == "Goal cancelled."


def test_refinement_cancellation_finishes_shared_state(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "goal-call")
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(G, "_emit_goal_notice", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        G,
        "refine_goal_spec_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            function_module.CancelledError("stop")
        ),
    )

    with pytest.raises(function_module.CancelledError):
        module.goal("do work", "done", runtime=_Runtime())
    assert G.load_goal("s1")["status"] == "error"


def test_work_agent_failure_finishes_shared_state(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "goal-call")
    monkeypatch.setattr(
        G, "refine_goal_spec_candidate", lambda *_args, **_kwargs: ("SPEC", []),
    )
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(G, "_emit_goal_notice", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent_module,
        "agent",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )

    with pytest.raises(RuntimeError, match="provider down"):
        module.goal("do work", "done", runtime=_Runtime())
    stored = G.load_goal("s1")
    assert stored["status"] == "error"
    assert "provider down" in stored["last_reason"]


def test_checklist_stall_is_shared_goal_state(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_goal(
        monkeypatch,
        db,
        agent_outputs=["one", "two", "three", "four"],
        verdicts=[("unmet", "same", "", [])] * 4,
        checklist=["a", "b"],
        max_rounds=10,
    )
    stored = G.load_goal("s1")
    assert stored["status"] == "error"
    assert "checklist stuck" in stored["last_reason"]


def test_goal_update_payload_includes_the_shared_state(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(
        "openprogram.events.emit_safe",
        lambda *args, **kwargs: events.append({"args": args, "kwargs": kwargs}),
    )
    monkeypatch.setattr("openprogram.webui.server._broadcast", lambda _raw: None)
    goal = {
        "text": "x",
        "status": "active",
        "turns_used": 1,
        "max_turns": 10,
        "checklist": [{"text": "a", "done": False}],
    }
    G._emit_goal_update(None, "s1", goal)
    assert events and events[0]["args"][0] == "goal.update"
    assert events[0]["args"][2]["goal"]["checklist"] == goal["checklist"]
