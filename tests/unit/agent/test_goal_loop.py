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
            "done",
            max_rounds=max_rounds,
            context_mode=context_mode,
            runtime=runtime or _Runtime(),
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
    opened: list = []
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
        consume_queue=[("answered", "方案 A")],
        opened=opened,
    )
    assert result == "finished"
    assert opened[0]["prompt"] == "A 还是 B？"
    # judge 的 [{label, description}] 在传给 open_question 前收敛成 label 列表
    # （PendingQuestion.options / 前端 ask 面板只认 list[str]）。
    assert opened[0]["options"] == ["A"]
    assert "方案 A" in prompts[1]
    stored = G.load_goal("s1")
    assert stored["status"] == "achieved"
    assert "last_question" not in stored


def test_needs_user_does_not_block_on_ask(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    opened: list = []
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input", "finished"],
        verdicts=[
            ("needs_user", "direction required", "A or B?", []),
            ("met", "done", "", []),
        ],
        runtime=runtime,
        opened=opened,
    )
    assert result == "finished"
    assert len(prompts) == 2
    assert runtime.questions == []
    assert len(opened) == 1
    assert "用户未回答" in prompts[1]


def test_unanswered_question_is_not_reopened(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list = []
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["one", "two", "finished"],
        verdicts=[
            ("needs_user", "choose", "A or B?", []),
            ("needs_user", "still", "A or B?", []),
            ("met", "done", "", []),
        ],
        opened=opened,
    )
    assert result == "finished"
    assert len(prompts) == 3
    assert len(opened) == 1


def test_consumed_answer_is_injected_next_round(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input", "finished"],
        verdicts=[
            ("needs_user", "choose", "A or B?", []),
            ("met", "done", "", []),
        ],
        consume_queue=[("answered", "用 A")],
    )
    assert result == "finished"
    assert "用 A" in prompts[1]
    assert "用户对上一项决定的回答" in prompts[1]


def test_unanswered_goal_question_degrades_and_continues(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拒答/超时/空答案不再杀 goal：降级为 unmet 自主续跑。"""
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input", "finished"],
        verdicts=[
            ("needs_user", "direction required", "A or B?", []),
            ("met", "done", "", []),
        ],
        runtime=_Runtime(),  # 无答案
    )
    assert result == "finished"
    assert len(prompts) == 2
    assert "用户未回答" in prompts[1]
    assert "自行选择最合理方案" in prompts[1]
    assert G.load_goal("s1")["status"] == "achieved"


def test_decline_with_one_round_budget_still_runs_autonomous_work(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拒答不计该提问轮：max_rounds=1 时第一次拒答不得直接 capped，
    工作 agent 还要再跑一轮（看到「用户未回答，自行选择」）。"""
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input", "chose myself"],
        verdicts=[
            ("needs_user", "direction required", "A or B?", []),
            ("met", "done", "", []),
        ],
        max_rounds=1,
        runtime=_Runtime(),
    )
    assert result == "chose myself"
    assert len(prompts) == 2
    assert "用户未回答" in prompts[1]
    assert G.load_goal("s1")["status"] == "achieved"


def test_answer_resets_the_turn_budget(
    db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有效回答重置 turns_used（OpenHands 式预算重置）：即使已到
    max_rounds，回答后循环仍继续。"""
    result, prompts = _run_goal(
        monkeypatch,
        db,
        agent_outputs=["need input", "after answer"],
        verdicts=[
            ("needs_user", "choose", "A or B?", []),
            ("met", "done", "", []),
        ],
        max_rounds=1,
        consume_queue=[("answered", "A")],
    )
    assert result == "after answer"
    assert len(prompts) == 2
    assert "A" in prompts[1]
    stored = G.load_goal("s1")
    assert stored["status"] == "achieved"
    assert stored["turns_used"] == 1  # 重置后只计回答后的那一轮


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
    assert stored["status"] == "error"
    assert "idle spin" in stored["last_reason"]
    assert stored["idle_rounds"] == 2


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
    module.goal("do work", "done", runtime=_Runtime())
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
