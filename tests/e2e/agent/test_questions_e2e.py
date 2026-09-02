"""端到端：runtime.ask 在执行上下文里发出 question.asked 事件、阻塞，
后台模拟前端答复 → resolve → ask 返回。串起 runtime + 事件层 + registry。
"""
from __future__ import annotations

import threading
import time

import pytest

import openprogram.execution as execution_module
from openprogram.events import WS_FRAME_EVENT, create_event_bus, get_event_bus
from openprogram.agent.questions import QuestionRegistry, get_question_registry
from openprogram.agent.run_control import (
    reset_current_execution_id,
    reset_current_session_id,
    set_current_execution_id,
    set_current_session_id,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore


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
        capabilities=CapabilitySet(pause=True),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="questions-e2e",
        ttl_seconds=30,
    )
    attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)

    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    # 隔离总线，抓 ws.frame
    bus = create_event_bus()
    import openprogram.events.bus as EB
    monkeypatch.setattr(EB, "_event_bus", bus)
    session_token = set_current_session_id("sess-e2e")
    execution_token = set_current_execution_id(execution.execution_id)
    yield bus, execution.execution_id
    reset_current_execution_id(execution_token)
    reset_current_session_id(session_token)


class _RT(Runtime):
    def __init__(self):  # 跳过真 provider 解析
        self.session_id = "op-test"


def test_ask_emits_frame_then_resumes_on_reply(fresh):
    bus, execution_id = fresh
    frames = []
    bus.subscribe(lambda ev: frames.append(ev.payload.get("frame")), types={WS_FRAME_EVENT})

    rt = _RT()
    result = {}

    def run_func():
        session_token = set_current_session_id("sess-e2e")
        execution_token = set_current_execution_id(execution_id)
        try:
            result["answer"] = rt.ask(
                "用哪个库？", options=["dayjs", "luxon"], timeout=5
            )
        finally:
            reset_current_execution_id(execution_token)
            reset_current_session_id(session_token)

    t = threading.Thread(target=run_func, daemon=True)
    t.start()

    # 等 question.asked 帧出现，拿到 question id
    qid = None
    for _ in range(50):
        for f in frames:
            if f and f.get("type") == "question.asked":
                qid = f["data"]["id"]
                break
        if qid:
            break
        time.sleep(0.02)
    assert qid, "question.asked 帧没发出"

    # 帧内容正确（前端契约）
    asked = next(f for f in frames if f.get("type") == "question.asked")["data"]
    assert asked["prompt"] == "用哪个库？"
    assert asked["options"] == ["dayjs", "luxon"]
    assert asked["session_id"] == "sess-e2e"

    # 模拟前端答复
    assert get_question_registry().resolve(qid, "answered", "luxon")
    t.join(timeout=3)
    assert result.get("answer") == "luxon"


def test_confirm_timeout_returns_default(fresh):
    _bus, _execution_id = fresh
    rt = _RT()
    # 没人答，超时 → default
    assert rt.confirm("继续？", timeout=0.05, default=False) is False
