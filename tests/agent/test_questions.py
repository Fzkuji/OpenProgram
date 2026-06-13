"""QuestionRegistry + runtime.ask/confirm 三态语义。"""
from __future__ import annotations

import threading
import time

import pytest

from openprogram.agent.questions import (
    AskTimeout, PendingQuestion, QuestionRegistry, UserDeclined,
    ask_blocking, get_question_registry, new_question_id,
)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    import openprogram.agent.questions as Q
    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    yield


# ─── registry ────────────────────────────────────────────────────────────────

def test_resolve_sets_event_and_value():
    reg = get_question_registry()
    q = PendingQuestion(id="q1", session_id="s", kind="ask", prompt="?")
    ev = reg.register(q)
    assert not ev.is_set()
    assert reg.resolve("q1", "answered", "luxon") is True
    assert ev.is_set()
    assert reg.consume("q1") == ("answered", "luxon")


def test_resolve_claim_once():
    reg = get_question_registry()
    reg.register(PendingQuestion(id="q1", session_id="s", kind="ask", prompt="?"))
    assert reg.resolve("q1", "answered", "a") is True
    assert reg.resolve("q1", "answered", "b") is False   # 第二次领取失败


def test_resolve_unknown_id():
    assert get_question_registry().resolve("nope", "answered", "x") is False


def test_list_and_cancel_session():
    reg = get_question_registry()
    reg.register(PendingQuestion(id="a", session_id="s1", kind="ask", prompt="?"))
    reg.register(PendingQuestion(id="b", session_id="s2", kind="ask", prompt="?"))
    assert {p.id for p in reg.list_pending("s1")} == {"a"}
    reg.cancel_session("s1")
    assert reg.list_pending("s1") == []
    assert reg.consume("a") == ("declined", None)


# ─── ask_blocking 三态 ────────────────────────────────────────────────────────

def test_ask_blocking_answered_from_other_thread():
    captured = {}
    def on_asked(q): captured["id"] = q.id

    def answer_later():
        time.sleep(0.05)
        get_question_registry().resolve(captured["id"], "answered", "dayjs")

    threading.Thread(target=answer_later, daemon=True).start()
    outcome, value = ask_blocking(
        session_id="s", kind="ask", prompt="lib?", timeout=5, on_asked=on_asked)
    assert (outcome, value) == ("answered", "dayjs")


def test_ask_blocking_timeout():
    outcome, value = ask_blocking(
        session_id="s", kind="ask", prompt="?", timeout=0.05,
        on_asked=lambda q: None)
    assert outcome == "timeout" and value is None


# ─── runtime.ask / confirm（用 fake runtime 不依赖 webui/LLM）─────────────────

class _FakeRuntime:
    """只复用 Runtime 的 ask/confirm 实现，跳过 __init__ 的 provider 解析。"""
    from openprogram.agentic_programming.runtime import Runtime
    ask = Runtime.ask
    confirm = Runtime.confirm
    can_ask = Runtime.can_ask
    _ask_raw = Runtime._ask_raw
    _ui_session_id = lambda self: "s"   # 假装有前端


def _answer_with(qid_box, outcome, value):
    def on_asked_capture():
        # by polling registry for the just-registered question
        pass
    def worker():
        time.sleep(0.05)
        reg = get_question_registry()
        ps = reg.list_pending()
        if ps:
            reg.resolve(ps[0].id, outcome, value)
    threading.Thread(target=worker, daemon=True).start()


def test_runtime_ask_returns_answer(monkeypatch):
    rt = _FakeRuntime()
    _answer_with(None, "answered", "luxon")
    assert rt.ask("lib?", timeout=5) == "luxon"


def test_runtime_ask_declined_raises(monkeypatch):
    rt = _FakeRuntime()
    _answer_with(None, "declined", None)
    with pytest.raises(UserDeclined):
        rt.ask("lib?", timeout=5)


def test_runtime_ask_timeout_default():
    rt = _FakeRuntime()
    assert rt.ask("lib?", timeout=0.05, default="fallback") == "fallback"


def test_runtime_ask_timeout_no_default_raises():
    rt = _FakeRuntime()
    with pytest.raises(AskTimeout):
        rt.ask("lib?", timeout=0.05)


def test_runtime_confirm_true():
    rt = _FakeRuntime()
    _answer_with(None, "answered", "确认")
    assert rt.confirm("go?", timeout=5) is True


def test_runtime_confirm_declined_false():
    rt = _FakeRuntime()
    _answer_with(None, "declined", None)
    assert rt.confirm("go?", timeout=5) is False


def test_runtime_confirm_timeout_default():
    rt = _FakeRuntime()
    assert rt.confirm("go?", timeout=0.05, default=False) is False
