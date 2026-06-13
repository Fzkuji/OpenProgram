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


def test_ask_blocking_timeout_retracts_card(monkeypatch):
    """超时要经 transport 收回前端卡片（广播 question.rejected），否则卡片挂死。"""
    import openprogram.agent.event_bus as EB
    frames = []
    monkeypatch.setattr(EB, "emit_ws_frame", lambda f: frames.append(f))
    monkeypatch.setattr(EB, "emit_safe", lambda *a, **k: None)
    outcome, _ = ask_blocking(
        session_id="s", kind="ask", prompt="?", timeout=0.05,
        on_asked=lambda q: None)
    assert outcome == "timeout"
    assert any(f.get("type") == "question.rejected" for f in frames)


# ─── runtime.ask / confirm（用 fake runtime 不依赖 webui/LLM）─────────────────

class _FakeRuntime:
    """只复用 Runtime 的 ask/confirm/form 实现，跳过 __init__ 的 provider 解析。"""
    from openprogram.agentic_programming.runtime import Runtime
    ask = Runtime.ask
    confirm = Runtime.confirm
    form = Runtime.form
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


# ─── runtime.form 三态（Phase 4a：多字段表单，答案是 dict）─────────────────────

def test_runtime_form_returns_dict():
    rt = _FakeRuntime()
    _answer_with(None, "answered", {"name": "Ada", "count": 3})
    assert rt.form("配置", {"name": {"type": "string"}}, timeout=5) == {
        "name": "Ada", "count": 3,
    }


def test_runtime_form_declined_raises():
    rt = _FakeRuntime()
    _answer_with(None, "declined", None)
    with pytest.raises(UserDeclined):
        rt.form("配置", {"name": {"type": "string"}}, timeout=5)


def test_runtime_form_timeout_default():
    rt = _FakeRuntime()
    assert rt.form("配置", {}, timeout=0.05, default={"name": "x"}) == {"name": "x"}


def test_runtime_form_timeout_no_default_raises():
    rt = _FakeRuntime()
    with pytest.raises(AskTimeout):
        rt.form("配置", {}, timeout=0.05)


def test_runtime_form_non_dict_answer_coerced_to_empty():
    """form 收到非 dict 答案（异常前端）时返回 {}，不把脏数据塞给调用方。"""
    rt = _FakeRuntime()
    _answer_with(None, "answered", "oops-not-a-dict")
    assert rt.form("配置", {"x": {"type": "string"}}, timeout=5) == {}


def test_form_schema_reaches_pending_question():
    """form 的字段 schema 进了 PendingQuestion（前端据此渲染多字段表单）。"""
    fields = {"name": {"type": "string", "title": "名字"},
              "mode": {"type": "string", "enum": ["fast", "slow"]}}
    captured = {}
    def worker():
        time.sleep(0.05)
        reg = get_question_registry()
        ps = reg.list_pending()
        if ps:
            captured["kind"] = ps[0].kind
            captured["schema"] = ps[0].schema
            reg.resolve(ps[0].id, "answered", {"name": "x", "mode": "fast"})
    threading.Thread(target=worker, daemon=True).start()
    rt = _FakeRuntime()
    rt.form("配置", fields, timeout=5)
    assert captured["kind"] == "form"
    assert captured["schema"] == fields


# ─── ask_user 内置原语接到 runtime.ask（复活老接口 + clarify 工具）──────────────

def test_ask_user_routes_to_runtime_ask(monkeypatch):
    """有前端执行上下文（runtime.can_ask）时，ask_user 走 runtime.ask 活链路，
    不再返回 None。"""
    import openprogram.functions.agentics.ask_user as A
    from openprogram.agentic_programming.function import _current_runtime

    # 没有旧全局 handler（webui 路径本来就没注册）
    A.set_ask_user(None)

    class _RT:
        def can_ask(self): return True
        def ask(self, q, **kw): return "来自 runtime.ask 的答案"

    token = _current_runtime.set(_RT())
    try:
        assert A.ask_user("随便问点啥？") == "来自 runtime.ask 的答案"
    finally:
        _current_runtime.reset(token)


def test_ask_user_no_runtime_no_handler_returns_none(monkeypatch):
    """无 handler、无可问 runtime、非 TTY → 老语义 None（不崩）。"""
    import openprogram.functions.agentics.ask_user as A
    from openprogram.agentic_programming.function import _current_runtime

    A.set_ask_user(None)
    token = _current_runtime.set(None)
    monkeypatch.setattr(A.sys, "stdin", None)
    try:
        assert A.ask_user("没人能答") is None
    finally:
        _current_runtime.reset(token)
