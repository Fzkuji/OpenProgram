"""Runtime interaction APIs consume only a durable pre-wait outcome."""
from __future__ import annotations

import pytest

import openprogram.execution as execution_module
from openprogram.agent.questions import (
    DurableWaitSafePointRequired, PendingQuestion, QuestionRegistry,
    get_question_registry,
)


@pytest.fixture(autouse=True)
def _execution(monkeypatch, tmp_path):
    import openprogram.agent.questions as questions
    from openprogram.agent.run_control import (
        reset_current_execution_id, set_current_execution_id,
    )
    from openprogram.execution.attempts import AttemptStore
    from openprogram.execution.model import CapabilitySet
    from openprogram.execution.store import ExecutionStore

    store = ExecutionStore(tmp_path / "questions.db")
    revision = store.create_revision(manifest={"entrypoint": "test"})
    execution = store.create_execution(
        execution_id="exec_questions", run_id="run_questions", session_id="s",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(pause=True, safe_point_kinds=("agent.wait.before_tool",)),
    )
    leased, reserved = AttemptStore(store).lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="test", ttl_seconds=30,
    )
    AttemptStore(store).activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    monkeypatch.setattr(questions, "_registry", QuestionRegistry())
    token = set_current_execution_id(execution.execution_id)
    yield store, execution.execution_id, leased
    reset_current_execution_id(token)


class _FakeRuntime:
    from openprogram.agentic_programming.runtime import Runtime
    ask = Runtime.ask
    confirm = Runtime.confirm
    form = Runtime.form
    _ask_raw = Runtime._ask_raw
    _ui_session_id = lambda self: "s"


def _resolved_wait(store, execution_id, attempt, *, kind, request, answer):
    """Install the result a prior safe-point handoff would make available."""
    from openprogram.agent.run_control import set_preapproved_wait_id
    from openprogram.execution.model import CommandKind
    from openprogram.execution.waits import DurableWaitStore

    wait_id = f"wait_{kind}_{len(DurableWaitStore(store).list_open())}"
    wait = DurableWaitStore(store).open_wait(
        wait_id=wait_id, execution_id=execution_id,
        attempt_id=attempt.attempt_id, generation=attempt.generation,
        kind=kind, request=request,
        policy_snapshot={"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        expires_at=9_999_999_999, checkpoint_id=None,
    )
    execution = store.get_execution(execution_id)
    assert execution is not None
    DurableWaitStore(store).resolve_with_command(
        command_id=f"answer-{wait_id}", execution_id=execution_id,
        expected_version=execution.status_version, actor={"surface": "test"},
        kind=CommandKind.WAIT_ANSWER, wait_id=wait.wait_id,
        generation=wait.claim_generation, answer=answer,
    )
    return set_preapproved_wait_id(wait_id)


def test_runtime_ask_requires_declared_safe_point(_execution):
    with pytest.raises(DurableWaitSafePointRequired) as raised:
        _FakeRuntime().ask("lib?")
    assert raised.value.code == "runtime_interaction_requires_safe_point"


@pytest.mark.parametrize(("kind", "wait_request", "answer", "invoke", "expected"), [
    ("ask", {"prompt": "lib?", "options": [], "multi": False, "allow_custom": True, "detail": "", "schema": {}, "questions": []}, "luxon", lambda rt: rt.ask("lib?"), "luxon"),
    ("ask_many", {"prompt": "", "options": [], "multi": False, "allow_custom": False, "detail": "", "schema": {}, "questions": [{"prompt": "role?", "options": ["a"], "multi": False, "allow_custom": True}]}, ["a"], lambda rt: rt.ask(questions=[{"prompt": "role?", "options": ["a"]}]), ["a"]),
    ("confirm", {"prompt": "go?", "options": ["确认", "取消"], "multi": False, "allow_custom": False, "detail": "", "schema": {}, "questions": []}, "确认", lambda rt: rt.confirm("go?"), True),
    ("form", {"prompt": "config", "options": [], "multi": False, "allow_custom": False, "detail": "", "schema": {"name": {"type": "string"}}, "questions": []}, {"name": "Ada"}, lambda rt: rt.form("config", {"name": {"type": "string"}}), {"name": "Ada"}),
])
def test_runtime_interaction_consumes_exact_pre_wait(
    _execution, kind, wait_request, answer, invoke, expected,
):
    store, execution_id, attempt = _execution
    from openprogram.agent.run_control import reset_preapproved_wait_id

    token = _resolved_wait(
        store, execution_id, attempt, kind=kind, request=wait_request, answer=answer,
    )
    try:
        assert invoke(_FakeRuntime()) == expected
    finally:
        reset_preapproved_wait_id(token)


def test_runtime_rejects_pre_wait_with_different_request(_execution):
    store, execution_id, attempt = _execution
    from openprogram.agent.run_control import reset_preapproved_wait_id

    token = _resolved_wait(
        store, execution_id, attempt, kind="ask",
        request={"prompt": "declared", "options": [], "multi": False, "allow_custom": True, "detail": "", "schema": {}, "questions": []},
        answer="a",
    )
    try:
        with pytest.raises(DurableWaitSafePointRequired):
            _FakeRuntime().ask("different")
    finally:
        reset_preapproved_wait_id(token)


def test_registry_is_only_a_wake_notifier(_execution):
    store, execution_id, attempt = _execution
    from openprogram.execution.waits import DurableWaitStore

    reg = get_question_registry()
    with pytest.raises(DurableWaitSafePointRequired):
        reg.register(PendingQuestion(id="missing", session_id="s", kind="ask", prompt="?"))
    wait = DurableWaitStore(store).open_wait(
        wait_id="wait_notifier", execution_id=execution_id,
        attempt_id=attempt.attempt_id, generation=attempt.generation,
        kind="ask", request={"prompt": "?"}, policy_snapshot={"version": 1},
        expires_at=9_999_999_999, checkpoint_id=None,
    )
    event = reg.register(PendingQuestion(
        id=wait.wait_id, session_id="s", kind="ask", prompt="?",
        execution_id=execution_id,
    ))
    assert not event.is_set()
    reg.wake(wait.wait_id)
    assert event.is_set()


def test_agent_loop_stops_before_a_declared_interaction_effect():
    import asyncio
    import time
    from openprogram.agent.agent_loop import _execute_tool_calls
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import AssistantMessage, TextContent, ToolCall
    from openprogram.providers.utils.event_stream import EventStream

    ran = False
    seen = []

    async def execute(*_args):
        nonlocal ran
        ran = True
        return AgentToolResult(content=[TextContent(text="unexpected")], details={}, is_error=False)

    tool = AgentTool(
        name="ask-tool", description="declares a question",
        parameters={"type": "object", "properties": {}}, label="ask-tool",
        execute=execute,
    )
    object.__setattr__(tool, "_interaction_manifest", lambda _id, _args: {
        "kind": "ask", "prompt": "Continue?", "options": ["yes", "no"],
        "allow_custom": False, "detail": "", "request_metadata": {},
        "policy_snapshot": {"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        "timeout": 30.0,
    })

    async def safe_point(kind, payload):
        seen.append((kind, payload["pre_wait"]))
        return True

    message = AssistantMessage(
        content=[ToolCall(id="call-ask", name="ask-tool", arguments={})],
        api="openai-completions", provider="openai", model="fake",
        stop_reason="toolUse", timestamp=int(time.time() * 1000),
    )
    outcome = asyncio.run(_execute_tool_calls(
        [tool], message, None, EventStream(), safe_point_hook=safe_point,
    ))
    assert outcome["stop_at_safe_point"] is True
    assert ran is False
    assert seen == [("tool.before", {
        "kind": "ask", "prompt": "Continue?", "options": ["yes", "no"],
        "allow_custom": False, "detail": "", "request_metadata": {},
        "policy_snapshot": {"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        "timeout": 30.0,
    })]
