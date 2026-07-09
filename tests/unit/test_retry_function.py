"""Unit tests for the function-call Retry button's backend.

The Retry button on a runtime-block sends the WS ``retry_function``
action. It must re-dispatch the SAME function with the SAME kwargs the
prior call used, in the SAME session — WITHOUT stripping any existing
messages (the old broken ``retry_overwrite`` path silently deleted them).

These cover ``ws_actions.chat._last_call_kwargs`` (kwargs lookup from the
authoritative DAG node) and ``handle_retry_function`` (re-dispatch wiring).
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.context.nodes import Call, ROLE_CODE, ROLE_USER
from openprogram.webui.ws_actions import chat


class _FakeDB:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_nodes(self, session_id):
        return list(self._nodes)


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


def _patch_db(monkeypatch, nodes):
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db",
        lambda: _FakeDB(nodes),
    )


# ---- _last_call_kwargs --------------------------------------------------

def test_last_call_kwargs_reads_latest_matching_node(monkeypatch):
    nodes = [
        Call(role=ROLE_CODE, name="word_count", input={"text": "old"}, seq=1),
        Call(role=ROLE_USER, name="user", input=None, seq=2),
        Call(role=ROLE_CODE, name="word_count", input={"text": "new"}, seq=3),
    ]
    _patch_db(monkeypatch, nodes)
    assert chat._last_call_kwargs("s1", "word_count") == {"text": "new"}


def test_last_call_kwargs_drops_injected_params(monkeypatch):
    nodes = [
        Call(role=ROLE_CODE, name="word_count",
             input={"text": "hi", "runtime": object(), "callback": object()},
             seq=1),
    ]
    _patch_db(monkeypatch, nodes)
    assert chat._last_call_kwargs("s1", "word_count") == {"text": "hi"}


def test_last_call_kwargs_none_when_never_called(monkeypatch):
    nodes = [Call(role=ROLE_CODE, name="other", input={"a": 1}, seq=1)]
    _patch_db(monkeypatch, nodes)
    assert chat._last_call_kwargs("s1", "word_count") is None


# ---- handle_retry_function ---------------------------------------------

def test_retry_redispatches_with_original_kwargs(monkeypatch):
    nodes = [
        Call(role=ROLE_CODE, name="word_count",
             input={"text": "hello world"}, seq=1),
    ]
    _patch_db(monkeypatch, nodes)

    calls = []

    def _fake_run(name, kwargs, session_id, work_dir=None):
        calls.append((name, kwargs, session_id))
        return {"session_id": session_id, "msg_id": "abc"}

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run
    )

    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(
        ws, {"session_id": "s1", "function": "word_count"}
    ))

    # Re-dispatched exactly once, with the prior call's kwargs + session.
    assert calls == [("word_count", {"text": "hello world"}, "s1")]
    # Acked the new run over the WS (so the client can follow the stream).
    assert ws.sent and "chat_ack" in ws.sent[0]


def test_retry_never_strips_messages_and_errors_without_prior_call(monkeypatch):
    # No prior word_count node → nothing to re-run. The handler must NOT
    # touch session messages (the old retry_overwrite bug); it broadcasts
    # a user-visible error instead and never calls the dispatcher.
    _patch_db(monkeypatch, [])

    dispatched = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *a, **k: dispatched.append(a) or {"session_id": "s1", "msg_id": "x"},
    )
    errors = []
    monkeypatch.setattr(
        "openprogram.webui.server._broadcast_chat_response",
        lambda sid, mid, env: errors.append(env),
    )

    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(
        ws, {"session_id": "s1", "function": "word_count"}
    ))

    assert dispatched == []               # never dispatched a bogus run
    assert errors and errors[0]["type"] == "error"
    assert "no prior" in errors[0]["content"].lower()


def test_retry_noop_on_missing_args(monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *a, **k: dispatched.append(a),
    )
    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(ws, {"function": "word_count"}))
    asyncio.run(chat.handle_retry_function(ws, {"session_id": "s1"}))
    assert dispatched == []
    assert ws.sent == []


def test_retry_overwrite_action_is_removed():
    # The dead legacy action must be gone from the dispatch table; the new
    # one present.
    assert "retry_overwrite" not in chat.ACTIONS
    assert chat.ACTIONS["retry_function"] is chat.handle_retry_function
