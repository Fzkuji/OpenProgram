"""端到端：runtime.ask 在执行上下文里发出 question.asked 事件、阻塞，
后台模拟前端答复 → resolve → ask 返回。串起 runtime + 事件层 + registry。
"""
from __future__ import annotations

import time
import asyncio

import pytest

import openprogram.execution as execution_module
from openprogram.events import WS_FRAME_EVENT, create_event_bus
from openprogram.agent.questions import (
    QuestionRegistry,
    _pending_from_wait,
    emit_question_asked,
    get_question_registry,
)
from openprogram.agent.run_control import (
    reset_current_execution_id,
    reset_current_session_id,
    set_current_execution_id,
    set_current_session_id,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore
from openprogram.execution.waits import DurableWaitStore, WaitStatus


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    import openprogram.agent.questions as Q

    store = ExecutionStore(tmp_path / "questions-e2e.db")
    revision = store.create_revision(manifest={"entrypoint": "questions-e2e"})
    execution = store.create_execution(
        execution_id="exec_questions_e2e",
        run_id="run_questions_e2e",
        session_id="sess-e2e",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("agent.wait.before_tool",),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="questions-e2e",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    async def activate(_attempt, _activation):
        return None

    control = RuntimeControlService(
        store, attempts, DriverRegistry(), activator=activate,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    monkeypatch.setattr(execution_module, "default_control_service", lambda: control)

    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    # 隔离总线，抓 ws.frame
    bus = create_event_bus()
    import openprogram.events.bus as EB
    monkeypatch.setattr(EB, "_event_bus", bus)
    session_token = set_current_session_id("sess-e2e")
    execution_token = set_current_execution_id(execution.execution_id)
    yield bus, execution.execution_id, store, control, active, running
    reset_current_execution_id(execution_token)
    reset_current_session_id(session_token)


class _RT(Runtime):
    def __init__(self):  # 跳过真 provider 解析
        self.session_id = "op-test"


def _open_predeclared_wait(fresh, *, kind: str, request: dict, timeout: float):
    """Create a question through the public safe-point handoff.

    The test then consumes the resolved durable record through the normal
    runtime entry point.  It does not create an ordinary open wait or install
    a process-local question record.
    """
    bus, execution_id, store, control, active, running = fresh
    wait_data = dict(request)

    def publish(suspension):
        q = _pending_from_wait(suspension.wait)
        emit_question_asked({
            "id": q.id,
            "session_id": q.session_id,
            "kind": q.kind,
            "prompt": q.prompt,
            "options": q.options,
            "multi": q.multi,
            "allow_custom": q.allow_custom,
            "detail": q.detail,
            "schema": q.schema,
            "questions": q.questions,
            "execution_id": q.execution_id,
            "wait_generation": q.wait_generation,
            "expected_version": q.execution_version,
            "expires_at": q.expires_at,
        })

    control.set_wait_suspension_observer(publish)
    return control.open_wait_at_safe_point(
        execution_id=execution_id,
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_version=running.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="agent.wait.before_tool",
            frontier=({"step_id": "question-e2e", "phase": "after_tool"},),
            state_refs={"continuation": {"version": 1}},
        ),
        kind=kind,
        request=wait_data,
        policy_snapshot={
            "version": 1,
            "on_answer": "continue",
            "on_decline": "fail",
            "on_timeout": "continue",
        },
        expires_at=time.time() + timeout,
    )


def test_ask_emits_frame_then_resumes_on_reply(fresh):
    bus, execution_id, store, control, _active, _running = fresh
    frames = []
    bus.subscribe(lambda ev: frames.append(ev.payload.get("frame")), types={WS_FRAME_EVENT})

    request = {
        "prompt": "用哪个库？", "options": ["dayjs", "luxon"],
        "multi": False, "allow_custom": True, "detail": "",
        "schema": {}, "questions": [],
    }
    suspension = _open_predeclared_wait(
        fresh, kind="ask", request=request, timeout=5,
    )
    qid = suspension.wait.wait_id
    assert frames and frames[-1]["type"] == "question.asked"

    # 帧内容正确（前端契约）
    asked = next(f for f in frames if f.get("type") == "question.asked")["data"]
    assert asked["prompt"] == "用哪个库？"
    assert asked["options"] == ["dayjs", "luxon"]
    assert asked["session_id"] == "sess-e2e"

    # 通过 canonical wait command 模拟前端答复；这会恢复 execution。
    assert get_question_registry().resolve(qid, "answered", "luxon")
    resumed = store.get_execution(execution_id)
    assert resumed is not None and resumed.status.value == "running"

    # 只有在真实 durable wait 已解析后，runtime.ask 才能消费它。
    from openprogram.agent.run_control import reset_preapproved_wait_id, set_preapproved_wait_id

    token = set_preapproved_wait_id(qid)
    try:
        assert _RT().ask("用哪个库？", options=["dayjs", "luxon"], timeout=5) == "luxon"
    finally:
        reset_preapproved_wait_id(token)


def test_confirm_timeout_recovers_execution(fresh):
    _bus, execution_id, store, control, _active, _running = fresh
    request = {
        "prompt": "继续？", "options": ["确认", "取消"],
        "multi": False, "allow_custom": False, "detail": "",
        "schema": {}, "questions": [],
    }
    suspension = _open_predeclared_wait(
        fresh, kind="confirm", request=request, timeout=0.05,
    )
    waits = DurableWaitStore(store)
    assert waits.expire_due(now=time.time() + 1) == 1
    expired = waits.get_wait(suspension.wait.wait_id)
    assert expired is not None and expired.status is WaitStatus.EXPIRED
    resumed = asyncio.run(control.recover_wait_outcomes())
    assert resumed and resumed[0].execution_id == execution_id
    current = store.get_execution(execution_id)
    assert current is not None and current.status.value == "running"

    # 超时结果和恢复状态都来自 durable wait；不会重新创建 ordinary wait。
    assert get_question_registry().consume(suspension.wait.wait_id) == ("timeout", None)
