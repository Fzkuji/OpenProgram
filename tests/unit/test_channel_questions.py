"""Phase 4b — channel /answer · /decline command handling + question
rendering. Pure-logic tests (no live channel adapter): the registry is
process-level, so we seed it and assert resolve / scope / mapping.
"""
import pytest

from openprogram.agent.questions import (
    PendingQuestion, get_question_registry,
)
from openprogram.channels._question_commands import (
    try_handle_question_command, _map_choice,
)
from openprogram.channels._question_bridge import _render_question


@pytest.fixture(autouse=True)
def _clean_registry():
    reg = get_question_registry()
    reg._pending.clear(); reg._events.clear(); reg._results.clear()
    yield
    reg._pending.clear(); reg._events.clear(); reg._results.clear()


def _seed(qid, session_id="s1", kind="ask", options=None, multi=False, schema=None):
    get_question_registry().register(PendingQuestion(
        id=qid, session_id=session_id, kind=kind, prompt="?",
        options=options or [], multi=multi, schema=schema or {}))


# ─── 命令拦截：归属 + 解析 ────────────────────────────────────────────────────

def test_non_command_falls_through():
    _seed("q1", session_id="s1")
    assert try_handle_question_command("hello there", "s1") is None


def test_answer_resolves_question_in_session():
    _seed("q1", session_id="s1", options=["dayjs", "luxon"])
    out = try_handle_question_command("/answer q1 2", "s1")
    assert out and "已记录" in out
    # registry resolved with the mapped option (1-based → luxon)
    assert get_question_registry().consume("q1") == ("answered", "luxon")


def test_answer_free_text_when_not_an_index():
    _seed("q1", session_id="s1", options=["a", "b"])
    try_handle_question_command("/answer q1 something custom", "s1")
    assert get_question_registry().consume("q1") == ("answered", "something custom")


def test_decline():
    _seed("q1", session_id="s1")
    out = try_handle_question_command("/decline q1", "s1")
    assert out and "拒绝" in out
    assert get_question_registry().consume("q1") == ("declined", None)


def test_answer_for_other_session_falls_through():
    """归属：q1 属于 s1；s2 的用户 /answer q1 不应 resolve（返回 None →
    当普通消息走 agent，而不是答掉别人会话的问题）。"""
    _seed("q1", session_id="s1")
    out = try_handle_question_command("/answer q1 x", "s2")
    assert out is None
    # 没被 resolve
    assert get_question_registry().consume("q1") is None


def test_answer_unknown_qid_falls_through():
    out = try_handle_question_command("/answer nope hi", "s1")
    assert out is None


def test_answer_without_id_falls_through():
    out = try_handle_question_command("/answer", "s1")
    assert out is None


# ─── choice 映射 ─────────────────────────────────────────────────────────────

def test_map_choice_index_1based():
    q = PendingQuestion(id="x", session_id="s", kind="ask", prompt="?",
                        options=["red", "green", "blue"])
    assert _map_choice(q, "1") == "red"
    assert _map_choice(q, "3") == "blue"


def test_map_choice_out_of_range_is_text():
    q = PendingQuestion(id="x", session_id="s", kind="ask", prompt="?",
                        options=["a", "b"])
    assert _map_choice(q, "5") == "5"


def test_map_choice_multi_comma():
    q = PendingQuestion(id="x", session_id="s", kind="ask", prompt="?",
                        options=["a", "b", "c"], multi=True)
    assert _map_choice(q, "1,3") == ["a", "c"]


# ─── 渲染（纯文本，含 /answer 提示）─────────────────────────────────────────

def test_render_options_includes_answer_command():
    txt = _render_question({"id": "q9", "kind": "ask", "prompt": "Pick",
                            "options": ["x", "y"]})
    assert "/answer q9" in txt
    assert "1) x" in txt and "2) y" in txt
    assert "/decline q9" in txt


def test_render_form_lists_fields():
    txt = _render_question({"id": "qF", "kind": "form", "prompt": "Config",
                            "schema": {"name": {"title": "名字"},
                                       "mode": {"enum": ["fast", "slow"]}}})
    assert "名字" in txt
    assert "fast/slow" in txt
    assert "/answer qF" in txt
