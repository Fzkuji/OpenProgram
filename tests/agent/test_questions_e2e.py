"""端到端：runtime.ask 在执行上下文里发出 question.asked 事件、阻塞，
后台模拟前端答复 → resolve → ask 返回。串起 runtime + 事件层 + registry。
"""
from __future__ import annotations

import threading
import time

import pytest

from openprogram.agent.event_bus import WS_FRAME_EVENT, create_event_bus, get_event_bus
from openprogram.agent.questions import QuestionRegistry, get_question_registry
from openprogram.agentic_programming.runtime import Runtime


@pytest.fixture
def fresh(monkeypatch):
    import openprogram.agent.questions as Q
    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    # 隔离总线，抓 ws.frame
    bus = create_event_bus()
    import openprogram.agent.event_bus as EB
    monkeypatch.setattr(EB, "_event_bus", bus)
    # 假装在 webui 执行上下文里（有 current_session_id）
    monkeypatch.setattr(Runtime, "_ui_session_id", lambda self: "sess-e2e")
    return bus


class _RT(Runtime):
    def __init__(self):  # 跳过真 provider 解析
        self.session_id = "op-test"


def test_ask_emits_frame_then_resumes_on_reply(fresh):
    bus = fresh
    frames = []
    bus.subscribe(lambda ev: frames.append(ev.payload.get("frame")), types={WS_FRAME_EVENT})

    rt = _RT()
    result = {}

    def run_func():
        result["answer"] = rt.ask("用哪个库？", options=["dayjs", "luxon"], timeout=5)

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
    rt = _RT()
    # 没人答，超时 → default
    assert rt.confirm("继续？", timeout=0.05, default=False) is False
