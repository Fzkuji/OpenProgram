"""Behavior of the single public Goal Workflow and its command adapter."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

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


def _stub_questions(
    monkeypatch: pytest.MonkeyPatch,
    *,
    consume_queue: list | None = None,
    opened: list | None = None,
) -> list:
    opened = opened if opened is not None else []
    pending = list(consume_queue or [])

    def fake_open_question(**kwargs):
        q = SimpleNamespace(
            id=f"qid-{len(opened) + 1}",
            session_id=kwargs.get("session_id") or "",
            kind=kwargs.get("kind") or "ask",
            prompt=kwargs.get("prompt") or "",
            options=list(kwargs.get("options") or []),
            multi=bool(kwargs.get("multi")),
            allow_custom=kwargs.get("allow_custom", True),
            detail=kwargs.get("detail") or "",
            schema=dict(kwargs.get("schema") or {}),
            questions=list(kwargs.get("questions") or []),
            expires_at=0.0,
        )
        opened.append(kwargs)
        on_asked = kwargs.get("on_asked")
        if on_asked:
            on_asked(q)
        return q, None

    class _Reg:
        def consume(self, qid):
            if pending:
                return pending.pop(0)
            return None

    questions = importlib.import_module("openprogram.agent.questions")
    monkeypatch.setattr(questions, "open_question", fake_open_question)
    monkeypatch.setattr(questions, "get_question_registry", lambda: _Reg())
    monkeypatch.setattr(questions, "emit_question_asked", lambda *_a, **_k: None)
    return opened


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
    tools_per_round: list[bool] | None = None,
    consume_queue: list | None = None,
    opened: list | None = None,
    resume: bool = False,
) -> tuple[str, list[str]]:
    _stub_questions(monkeypatch, consume_queue=consume_queue, opened=opened)
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

    # 模拟 ambient Runtime 的 last_blocks（零工具轮 → 空列表）。
    class _RuntimeStub:
        last_blocks: list = []

    stub = _RuntimeStub()
    tool_flags = iter(tools_per_round or [])
    token = None
    if tools_per_round is not None:
        token = function_module._current_runtime.set(stub)

    def fake_agent(**kwargs):
        prompts.append(kwargs["prompt"])
        if tools_per_round is not None:
            stub.last_blocks = (
                [{"type": "tool", "tool": "bash"}]
                if next(tool_flags, True) else []
            )
        return next(outputs)

    decisions = iter(verdicts)
    monkeypatch.setattr(agent_module, "agent", fake_agent)
    monkeypatch.setattr(
        G,
        "evaluate_goal",
        lambda *_args, **_kwargs: next(decisions),
    )
    try:
        result = module.goal(
            "do work",
            max_rounds=max_rounds,
            context_mode=context_mode,
            runtime=runtime or _Runtime(),
            resume=resume,
        )
    finally:
        if token is not None:
            function_module._current_runtime.reset(token)
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
    assert G.load_goal("s1")["status"] == "cancelled"
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
    assert G.load_goal("s1")["status"] == "budget_exhausted"

    _run_goal(
        monkeypatch,
        db,
        agent_outputs=["one"],
        verdicts=[("judge_failure", "bad", "", [])],
        max_rounds=1,
    )
    assert G.load_goal("s1")["status"] == "budget_exhausted"

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
    assert stored["status"] == "failed"
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


def test_needs_user_persists_and_stops_before_another_work_round(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input"],
        verdicts=[(
            "needs_user",
            "direction required",
            "A 还是 B？",
            [{"label": "A", "description": "first"}],
        )],
    )
    assert result == "need input"
    assert len(prompts) == 1
    assert prompts[0].startswith("do work\n")
    assert "Human questions are asynchronous" in prompts[0]
    stored = G.load_goal("s1")
    assert stored["status"] == "waiting_user"
    assert stored["last_question"] == "A 还是 B？"
    assert stored["questions"][0]["options"] == [
        {"label": "A", "description": "first"},
    ]


def test_second_resume_preserves_consumed_answers_and_work_evidence(db, monkeypatch, tmp_path):
    G.save_goal("s1", {
        "text": "survey", "status": "waiting_user", "version": 0,
        "questions": [{"id": "scope", "prompt": "Scope?", "status": "pending"}],
    })
    G.apply_goal_action("s1", "answer", question_id="scope", answer="RAG_ONLY_482")
    _run_goal(
        monkeypatch, db, resume=True, agent_outputs=["draft saved at artifact-482.md"],
        verdicts=[("waiting_external", "waiting for job", "", [], False)],
    )
    # Reopen persistence so the second invocation cannot rely on run-local state.
    reopened = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr(G, "_db", lambda: reopened)
    views = []
    def capture(*args, **kwargs):
        views.append(kwargs.get("session_view", ""))
        return "met", "done", "", [], False
    # _run_goal installs a verdict stub; capture the arguments at its prompt seam
    # instead in a standalone invocation using the already-installed work stub.
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    prompts = []
    monkeypatch.setattr(agent_module, "agent", lambda **kw: prompts.append(kw["prompt"]) or "done")
    monkeypatch.setattr(G, "evaluate_goal", capture)
    module.goal("survey", resume=True)
    assert "RAG_ONLY_482" in prompts[0]
    assert "RAG_ONLY_482" in views[0]
    assert "artifact-482.md" in prompts[0]
    assert "artifact-482.md" in views[0]
    G.apply_goal_action("s1", "edit", prompt="new independent task")
    module.goal("new independent task", resume=True)
    assert "RAG_ONLY_482" not in prompts[-1]
    assert "artifact-482.md" not in prompts[-1]
    assert "RAG_ONLY_482" not in views[-1]


def test_resume_without_answer_remains_waiting_and_does_not_run_agent(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input"],
        verdicts=[("needs_user", "choose", "A or B?", [])],
    )
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=[],
        verdicts=[],
        resume=True,
    )
    assert result == ""
    assert prompts == []
    assert G.load_goal("s1")["status"] == "waiting_user"


def test_durable_answer_resumes_with_same_goal_and_cumulative_budget(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input"],
        verdicts=[("needs_user", "choose", "A or B?", [])],
        max_rounds=3,
    )
    before = G.load_goal("s1")
    G.apply_goal_action("s1", "answer", answer="用 A")
    answered = G.load_goal("s1")
    assert answered["pending_answers"][0]["answer"] == "用 A"
    assert answered["status"] == "paused"

    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["finished"],
        verdicts=[("met", "done", "", [])],
        resume=True,
    )
    after = G.load_goal("s1")
    assert result == "finished"
    assert "用 A" in prompts[0]
    assert after["goal_id"] == before["goal_id"]
    assert after["run_id"] != before["run_id"]
    assert after["turns_used"] == 2
    assert after["status"] == "achieved"
    assert after["pending_answers"] == []


def test_async_question_allows_independent_work_before_waiting(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["found ambiguity", "completed independent work"],
        verdicts=[
            ("needs_user", "scope is unclear", "Include patents?", [], True),
            ("needs_user", "only patent section remains", "Include patents?", [], False),
        ],
    )
    assert result == "completed independent work"
    assert len(prompts) == 2
    assert "不要猜测或执行依赖这些答案的事项" in prompts[1]
    stored = G.load_goal("s1")
    assert stored["status"] == "waiting_user"
    assert len(stored["questions"]) == 1
    assert stored["questions"][0]["can_continue"] is False


def test_answering_one_question_allows_newly_unblocked_work(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)
    G.save_goal("s1", {
        "text": "write survey",
        "status": "waiting_user",
        "version": 0,
        "questions": [
            {"id": "scope", "prompt": "Which scope?", "status": "pending"},
            {"id": "venue", "prompt": "Which venue?", "status": "pending"},
        ],
    })

    first = G.apply_goal_action(
        "s1", "answer", question_id="scope", answer="Knowledge editing",
    )
    assert first["status"] == "paused"
    assert first["phase"] == "answer_received"
    assert [item["id"] for item in first["questions"] if item["status"] == "pending"] == ["venue"]

    second = G.apply_goal_action(
        "s1", "answer", question_id="venue", answer="NeurIPS",
    )
    assert second["status"] == "paused"
    assert len(second["pending_answers"]) == 2


def test_answer_does_not_resume_a_user_paused_goal(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)
    G.save_goal("s1", {
        "text": "survey", "status": "paused", "phase": "paused", "version": 0,
        "questions": [{"id": "scope", "prompt": "Which scope?", "status": "pending"}],
    })

    result = G.handle_goal_command("s1", "answer scope Editing")

    assert "invoke" not in result
    assert G.load_goal("s1")["status"] == "paused"
    assert G.load_goal("s1")["pending_answers"][0]["answer"] == "Editing"


def test_resume_with_exhausted_budget_does_not_start_work(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module("openprogram.agentic_programming.function")
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)
    monkeypatch.setattr(G, "_emit_goal_notice", lambda *_a, **_k: None)
    G.save_goal("s1", {
        "text": "survey", "spec": "survey", "status": "budget_exhausted",
        "version": 0, "turns_used": 1, "budget": {"max_turns": 1},
    })

    def unexpected_work(**_kwargs):
        pytest.fail("exhausted Goal started another working turn")

    monkeypatch.setattr(agent_module, "agent", unexpected_work)
    assert module.goal("survey", resume=True) == ""
    assert G.load_goal("s1")["status"] == "budget_exhausted"
    assert G.load_goal("s1")["recoverable"] is True


def test_answer_arriving_during_judgment_supersedes_stale_verdict(
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
        G, "refine_goal_spec_candidate", lambda *_a, **_k: ("SPEC", []),
    )
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)
    prompts: list[str] = []
    outputs = iter(["first result", "answer-aware result"])

    def work(**kwargs):
        prompts.append(kwargs["prompt"])
        return next(outputs)

    decisions = 0

    def judge(*_args, **_kwargs):
        nonlocal decisions
        decisions += 1
        if decisions == 1:
            latest = G.load_goal("s1")
            latest["questions"] = [{
                "id": "scope",
                "prompt": "Which scope?",
                "status": "pending",
            }]
            G.save_goal("s1", latest)
            G.apply_goal_action(
                "s1", "answer", question_id="scope", answer="Knowledge editing",
            )
            return "needs_user", "stale decision", "Which scope?", [], False
        return "met", "done", "", [], False

    monkeypatch.setattr(agent_module, "agent", work)
    monkeypatch.setattr(G, "evaluate_goal", judge)

    assert module.goal("do work", max_rounds=4) == "answer-aware result"
    assert decisions == 2
    assert "Knowledge editing" in prompts[1]
    assert G.load_goal("s1")["status"] == "achieved"


def test_elapsed_budget_counts_active_time_not_waiting_time() -> None:
    goal = {
        "budget": {"max_elapsed_s": 10},
        "usage": {"active_elapsed_s": 3.0},
        "active_started_at": 100.0,
    }
    G.checkpoint_active_elapsed(goal, now=104.0, stop=True)
    assert goal["usage"]["active_elapsed_s"] == 7.0
    assert goal["active_started_at"] is None
    assert G.budget_exhausted(goal, now=10_000.0) == ""

    goal["active_started_at"] = 20_000.0
    assert G.budget_exhausted(goal, now=20_003.0) == "elapsed_time"


def test_usage_cursor_excludes_session_activity_while_goal_is_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    totals = iter([
        {"total_tokens": 100, "cost_usd": 1.0, "unknown_cost_events": 0},
        {"total_tokens": 130, "cost_usd": 1.3, "unknown_cost_events": 0},
        {"total_tokens": 500, "cost_usd": 5.0, "unknown_cost_events": 0},
        {"total_tokens": 520, "cost_usd": 5.2, "unknown_cost_events": 0},
    ])
    monkeypatch.setattr(G, "goal_usage", lambda *_a, **_k: next(totals))
    goal = {
        "usage": {
            "total_tokens": 0,
            "cost_usd": 0.0,
            "cost_known": True,
            "active_elapsed_s": 0.0,
        },
    }

    G.reset_goal_usage_cursor("s1", goal)
    G.accumulate_goal_usage("s1", goal)
    assert goal["usage"]["total_tokens"] == 30
    assert goal["usage"]["cost_usd"] == pytest.approx(0.3)

    # The jump from 130 to 500 occurred while the Goal was waiting. Resetting
    # the cursor on resume excludes it; only the next active-run delta counts.
    G.reset_goal_usage_cursor("s1", goal)
    G.accumulate_goal_usage("s1", goal)
    assert goal["usage"]["total_tokens"] == 50
    assert goal["usage"]["cost_usd"] == pytest.approx(0.5)


def test_budget_action_rejects_negative_or_non_finite_limits(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)
    G.save_goal("s1", {"text": "x", "status": "paused", "version": 0})
    for key, value in (("max_turns", -1), ("max_cost_usd", "nan")):
        with pytest.raises(ValueError, match="positive number or zero"):
            G.apply_goal_action("s1", "budget", **{key: value})


def test_edit_supersedes_old_pending_questions(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)
    G.save_goal("s1", {
        "text": "old objective",
        "status": "waiting_user",
        "version": 0,
        "questions": [
            {"id": "scope", "prompt": "Which scope?", "status": "pending"},
            {"id": "done", "prompt": "Answered", "status": "answered"},
        ],
        "pending_answers": [{"question_id": "done", "answer": "A"}],
        "last_question": "Which scope?",
        "last_question_id": "scope",
        "last_question_options": [{"label": "A", "description": ""}],
    })

    edited = G.apply_goal_action("s1", "edit", prompt="new objective")

    assert edited["status"] == "paused"
    assert edited["pending_answers"] == []
    assert [item["status"] for item in edited["questions"]] == [
        "superseded", "answered",
    ]
    assert "last_question" not in edited
    assert "last_question_id" not in edited
    assert "last_question_options" not in edited


def test_tui_status_lists_all_pending_questions_and_can_answer_by_id(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)
    G.save_goal("s1", {
        "text": "survey",
        "status": "waiting_user",
        "version": 0,
        "questions": [
            {"id": "scope", "prompt": "Which scope?", "status": "pending"},
            {"id": "venue", "prompt": "Which venue?", "status": "pending"},
        ],
    })
    status = G.handle_goal_command("s1", "")["text"]
    assert "pending questions: 2" in status
    assert "scope: Which scope?" in status
    assert "venue: Which venue?" in status

    result = G.handle_goal_command("s1", "answer venue NeurIPS")
    assert result["invoke"]["name"] == "goal"
    stored = G.load_goal("s1")
    assert stored["status"] == "paused"
    assert stored["pending_answers"][0]["question_id"] == "venue"


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
        "do work", runtime=_Runtime(),
    ) == "stopped"
    assert G.load_goal("s1")["status"] == "cancelled"


@pytest.mark.parametrize("phase", ["evaluating", "terminal"])
@pytest.mark.parametrize("action", ["budget", "answer"])
def test_goal_keeps_same_run_updates_at_the_cas_boundary(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch, phase: str, action: str,
) -> None:
    save = G.save_goal
    injected = False

    def racing_save(sid, state):
        nonlocal injected
        if state.get("phase") == "working" and not state.get("questions"):
            state["questions"] = [{
                "id": "scope", "prompt": "Which scope?", "status": "pending",
                "can_continue": True,
            }]
        if not injected and state.get("phase") == phase:
            injected = True
            if action == "budget":
                G.apply_goal_action(sid, "budget", max_turns=200)
            else:
                G.apply_goal_action(sid, "answer", question_id="scope", answer="Use RAG")
        return save(sid, state)

    monkeypatch.setattr(G, "save_goal", racing_save)
    _, prompts = _run_goal(
        monkeypatch, db, agent_outputs=["draft", "revised"],
        verdicts=[("met", "verified", "", [])] * 2,
        tools_per_round=[True, True], max_rounds=3,
    )
    assert injected
    stored = G.load_goal("s1")
    assert stored["turns_used"] >= 1
    if action == "budget":
        assert stored["budget"]["max_turns"] == 200
        assert stored["status"] == "achieved"
    else:
        assert stored["questions"][0]["status"] == "answered"
        if phase == "terminal":
            assert stored["status"] == "paused_recoverable"
            assert stored["recoverable"] is True
            assert stored["pending_answers"][0]["answer"] == "Use RAG"
        else:
            assert stored["status"] == "achieved"
            assert len(prompts) == 2
            assert "Use RAG" in prompts[-1]
            assert stored["turns_used"] == 2


@pytest.mark.parametrize("action", ["pause", "cancel", "replace"])
def test_cas_rebase_never_overwrites_a_stopped_or_replaced_run(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    from openprogram.agentic_programming.function import CancelledError
    save = G.save_goal
    injected = False
    monkeypatch.setattr("openprogram.agent.run_control.mark_cancelled", lambda *_a, **_k: None)

    def racing_save(sid, state):
        nonlocal injected
        if not injected and state.get("phase") == "evaluating":
            injected = True
            if action == "replace":
                latest = G.load_goal(sid)
                save(sid, {**latest, "goal_id": "replacement", "run_id": "new-run"})
            else:
                G.apply_goal_action(sid, action)
        return save(sid, state)

    monkeypatch.setattr(G, "save_goal", racing_save)
    with pytest.raises(CancelledError):
        _run_goal(monkeypatch, db, agent_outputs=["old work"], verdicts=[])
    stored = G.load_goal("s1")
    assert injected
    if action == "replace":
        assert stored["goal_id"] == "replacement"
        assert stored["run_id"] == "new-run"
    else:
        assert stored["status"] == {"pause": "paused", "cancel": "cancelled"}[action]


def test_answer_arriving_while_consuming_preserves_both_decisions(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    G.save_goal("s1", {
        "goal_id": "survey", "run_id": "previous", "text": "write survey",
        "spec": "SPEC", "status": "paused", "version": 0,
        "questions": [
            {"id": "scope", "prompt": "Scope?", "status": "answered", "answer": "RAG only"},
            {"id": "venue", "prompt": "Venue?", "status": "pending"},
        ],
        "pending_answers": [{"question_id": "scope", "prompt": "Scope?", "answer": "RAG only"}],
    })
    save = G.save_goal
    injected = False

    def racing_save(sid, state):
        nonlocal injected
        if not injected and state.get("phase") == "answers_consumed":
            injected = True
            G.apply_goal_action(sid, "answer", question_id="venue", answer="NeurIPS")
        return save(sid, state)

    monkeypatch.setattr(G, "save_goal", racing_save)
    _, prompts = _run_goal(
        monkeypatch, db, resume=True, agent_outputs=["article"],
        verdicts=[("met", "verified", "", [])],
    )
    assert injected
    assert "RAG only" in prompts[0]
    assert "NeurIPS" in prompts[0]
    stored = G.load_goal("s1")
    assert stored["pending_answers"] == []
    assert all(item["status"] == "answered" for item in stored["questions"])


def test_new_goal_replaces_running_goal_without_old_controller_adopting_it(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("openprogram.programs.workflow.goal.goal")
    agent_module = importlib.import_module("openprogram.agentic_programming.agent")
    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "current_session_id", lambda: "s1")
    monkeypatch.setattr(function_module, "current_call_id", lambda: "old-call")
    monkeypatch.setattr(
        G, "refine_goal_spec_candidate", lambda *_a, **_k: ("OLD SPEC", []),
    )
    monkeypatch.setattr(G, "_emit_goal_update", lambda *_a, **_k: None)

    def replace_goal(**_kwargs):
        previous = G.load_goal("s1")
        G.save_goal("s1", {
            "goal_id": "new-goal",
            "run_id": "new-run",
            "text": "new objective",
            "status": "active",
            "version": previous["version"],
        })
        return "old result"

    monkeypatch.setattr(agent_module, "agent", replace_goal)
    monkeypatch.setattr(
        G,
        "evaluate_goal",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("the old controller must not judge the new Goal")
        ),
    )

    assert module.goal("old objective") == "old result"
    stored = G.load_goal("s1")
    assert stored["goal_id"] == "new-goal"
    assert stored["text"] == "new objective"


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
        assert G.handle_goal_command("s1", "clear")["text"] == "Goal cancelled."
        return "met", "done", "", []

    monkeypatch.setattr(G, "evaluate_goal", clear_then_accept)
    assert module.goal("do work", runtime=_Runtime()) == "finished"
    assert G.load_goal("s1")["status"] == "cancelled"


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
        assert G.handle_goal_command("s1", "clear")["text"] == "Goal cancelled."
        return "SPEC", ["item"]

    monkeypatch.setattr(G, "refine_goal_spec_candidate", clear_during_refinement)
    monkeypatch.setattr(
        agent_module,
        "agent",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("a cleared refinement must not start work")
        ),
    )

    assert module.goal("do work", runtime=_Runtime()) == ""
    assert G.load_goal("s1")["status"] == "cancelled"


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
        module.goal("do work", runtime=_Runtime())
    stored = G.load_goal("s1")
    assert stored["status"] == "failed"
    assert stored["last_reason"] == "Goal execution was cancelled unexpectedly."


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
        module.goal("do work", runtime=_Runtime())
    assert G.load_goal("s1")["status"] == "failed"


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
        module.goal("do work", runtime=_Runtime())
    stored = G.load_goal("s1")
    assert stored["status"] == "failed"
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
    assert stored["status"] == "stalled"
    assert "checklist stuck" in stored["last_reason"]


def test_default_max_turns_is_150_when_config_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openprogram.setup as setup_module
    monkeypatch.setattr(setup_module, "_read_config", lambda: {})
    assert G.default_max_turns() == G.DEFAULT_MAX_TURNS == 150


def test_explicit_zero_or_negative_max_turns_means_unlimited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openprogram.setup as setup_module
    for value in (0, -1, "0"):
        monkeypatch.setattr(
            setup_module, "_read_config",
            lambda v=value: {"goal": {"max_turns": v}},
        )
        assert G.default_max_turns() is None
    monkeypatch.setattr(
        setup_module, "_read_config", lambda: {"goal": {"max_turns": 7}},
    )
    assert G.default_max_turns() == 7


def test_zero_tool_round_warns_then_second_terminates(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["talk only", "still talk"],
        verdicts=[
            ("unmet", "no progress", "", []),
            ("unmet", "no progress", "", []),
        ],
        tools_per_round=[False, False],
    )
    # 第一次零工具 → 下一轮 prompt 带明确警告
    assert len(prompts) == 2
    assert "必须实际动手使用工具" in prompts[1]
    stored = G.load_goal("s1")
    assert stored["status"] == "stalled"
    assert "idle spin" in stored["last_reason"]
    assert stored["idle_rounds"] == 2


@pytest.mark.parametrize("prior_rounds,used_tools,checklist", [
    (1, False, []),
    (3, True, ["needs approval"]),
])
def test_required_answer_waits_instead_of_stalling(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
    prior_rounds: int, used_tools: bool, checklist: list[str],
) -> None:
    _run_goal(
        monkeypatch, db,
        agent_outputs=["investigated"] * prior_rounds + ["approval required"],
        verdicts=[("unmet", "investigating", "", [])] * prior_rounds
        + [("needs_user", "authorization missing", "Approve action?", [], False)],
        tools_per_round=[used_tools] * (prior_rounds + 1),
        checklist=checklist,
    )
    stored = G.load_goal("s1")
    assert stored["status"] == "waiting_user"
    question = stored["questions"][0]
    assert question["status"] == "pending"
    response = G.handle_goal_command("s1", f"answer {question['id']} yes")
    assert response["invoke"]["name"] == "goal"
    assert response["invoke"]["kwargs"]["resume"] is True


def test_tool_use_resets_the_idle_counter(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["talk only", "did work", "finished"],
        verdicts=[
            ("unmet", "no progress", "", []),
            ("unmet", "keep going", "", []),
            ("met", "done", "", []),
        ],
        tools_per_round=[False, True, True],
    )
    assert result == "finished"
    # 第二轮用了工具 → 计数清零，第三轮 prompt 不再带警告
    assert "必须实际动手使用工具" in prompts[1]
    assert "必须实际动手使用工具" not in prompts[2]
    stored = G.load_goal("s1")
    assert stored["status"] == "achieved"
    assert stored["idle_rounds"] == 0


def test_judge_evidence_is_tail_truncated(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.programs.workflow.goal.judge import VIEW_TAIL_MAX_CHARS

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
        agent_module, "agent", lambda **_kwargs: "x" * (VIEW_TAIL_MAX_CHARS + 5000),
    )
    seen: list[str] = []

    def capture(*_args, **kwargs):
        seen.append(kwargs["session_view"])
        return "met", "done", "", []

    monkeypatch.setattr(G, "evaluate_goal", capture)
    module.goal("do work", runtime=_Runtime())
    assert len(seen) == 1
    assert len(seen[0]) == VIEW_TAIL_MAX_CHARS
    assert seen[0].startswith("[earlier evidence truncated]\n")


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
        "last_question_at": 12.5,
        "checklist": [{"text": "a", "done": False}],
    }
    G._emit_goal_update(None, "s1", goal)
    assert events and events[0]["args"][0] == "goal.update"
    payload = events[0]["args"][2]["goal"]
    assert payload["checklist"] == goal["checklist"]
    assert payload["last_question_at"] == 12.5


def test_goal_snapshot_rejects_a_stale_version(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    G.save_goal("s1", {"text": "x", "status": "active", "version": 0})
    first = G.load_goal("s1")
    stale = G.load_goal("s1")
    first["status"] = "paused"
    G.save_goal("s1", first)
    stale["status"] = "achieved"
    with pytest.raises(G.GoalConflictError):
        G.save_goal("s1", stale)
    assert G.load_goal("s1")["status"] == "paused"
