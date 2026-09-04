"""Channel question commands translate to durable execution wait commands."""
import time

import pytest

import openprogram.agent.questions as Q
import openprogram.execution as execution_module
from openprogram.agent.questions import (
    PendingQuestion, QuestionRegistry, get_question_registry,
)
from openprogram.agent.run_control import reset_current_execution_id, set_current_execution_id
from openprogram.agent.authority import owner_authority
from openprogram.channels._question_commands import (
    try_handle_question_command, _map_choice,
)
from openprogram.channels._question_bridge import _render_question
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore
from openprogram.execution.waits import DurableWaitStore


_STORE = None
_EXECUTION = None
_ATTEMPT = None
_ACTOR = {
    **owner_authority("owner/install/0123456789abcdef"),
    "project_ids": ["default"],
    "session_ids": ["s1"],
    "execution_actions": ["execution.wait.answer", "execution.wait.decline"],
}


@pytest.fixture(autouse=True)
def _durable_registry(monkeypatch, tmp_path):
    global _STORE, _EXECUTION, _ATTEMPT
    store = ExecutionStore(tmp_path / "channels.db")
    revision = store.create_revision(manifest={"entrypoint": "channel"})
    execution = store.create_execution(
        execution_id="exec_channel", run_id="run_channel", session_id="s1",
        revision_id=revision.revision_id, capabilities=CapabilitySet(pause=True),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="channel", ttl_seconds=30,
    )
    _attempt, _running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    monkeypatch.setattr(Q, "_registry", QuestionRegistry())
    _STORE, _EXECUTION, _ATTEMPT = store, execution, _attempt
    token = set_current_execution_id(execution.execution_id)
    yield
    reset_current_execution_id(token)


def _seed(qid, session_id="s1", kind="ask", options=None, multi=False, schema=None):
    del session_id
    request = {
        "prompt": "?", "options": options or [], "multi": multi,
        "allow_custom": True, "detail": "", "schema": schema or {},
        "questions": [],
    }
    wait = DurableWaitStore(_STORE).open_wait(
        wait_id=qid, execution_id=_EXECUTION.execution_id,
        attempt_id=_ATTEMPT.attempt_id, generation=_ATTEMPT.generation,
        kind=kind, request=request, policy_snapshot={"version": 1},
        expires_at=time.time() + 60,
    )
    get_question_registry().register(PendingQuestion(
        id=wait.wait_id, session_id=_EXECUTION.session_id, kind=kind,
        prompt="?", execution_id=_EXECUTION.execution_id))


# 命令拦截：归属 + 解析

def test_non_command_falls_through():
    _seed("q1", session_id="s1")
    assert try_handle_question_command("hello there", "s1", actor=_ACTOR) is None


def test_answer_resolves_question_in_session():
    _seed("q1", session_id="s1", options=["dayjs", "luxon"])
    out = try_handle_question_command("/answer q1 2", "s1", actor=_ACTOR)
    assert out and "已记录" in out
    # registry resolved with the mapped option (1-based → luxon)
    assert get_question_registry().consume("q1") == ("answered", "luxon")


def test_answer_free_text_when_not_an_index():
    _seed("q1", session_id="s1", options=["a", "b"])
    try_handle_question_command("/answer q1 something custom", "s1", actor=_ACTOR)
    assert get_question_registry().consume("q1") == ("answered", "something custom")


def test_decline():
    _seed("q1", session_id="s1")
    out = try_handle_question_command("/decline q1", "s1", actor=_ACTOR)
    assert out and "拒绝" in out
    assert get_question_registry().consume("q1") == ("declined", None)


def test_answer_for_other_session_falls_through():
    """归属：q1 属于 s1；s2 的用户 /answer q1 不应 resolve（返回 None →
    当普通消息走 agent，而不是答掉别人会话的问题）。"""
    _seed("q1", session_id="s1")
    out = try_handle_question_command("/answer q1 x", "s2", actor=_ACTOR)
    assert out is None
    # 没被 resolve
    assert get_question_registry().consume("q1") is None


def test_answer_unknown_qid_falls_through():
    out = try_handle_question_command("/answer nope hi", "s1", actor=_ACTOR)
    assert out is None


def test_answer_without_id_falls_through():
    out = try_handle_question_command("/answer", "s1", actor=_ACTOR)
    assert out is None


# choice 映射

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


# 渲染（纯文本，含 /answer 提示）

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
