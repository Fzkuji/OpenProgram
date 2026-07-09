"""Parent-side pre-creation of a function run's top-level code node.

To move the UI to a new run in ~0.2s (decoupled from the spawned child's
~1s import), ``run_agentic_function_call`` pre-creates the run's top-level
code node in the PARENT before spawning, and threads its id to the child so
the @agentic_function wrapper REUSES it instead of appending a duplicate.

These lock:
  1. parent pre-creates the node + advances head before dispatch returns,
  2. the child (wrapper with ``_forced_node_id`` set) reuses that id and
     leaves exactly one top-level node — the exit update flips its status,
  3. ``create_pending_call_node`` builds the same shape the wrapper writes,
  4. a child error marks the pre-created "running" node so it doesn't spin
     forever.
"""
from __future__ import annotations

import threading

import pytest

from openprogram.store import GraphStoreShim, _store as _store_var
from openprogram.store.session.session_store import SessionStore


def _store(tmp_path) -> SessionStore:
    s = SessionStore(tmp_path / "sessions-git")
    s.create_session("s1", "main", title="t")
    return s


def _head(store) -> str | None:
    return (store.get_session("s1") or {}).get("head_id")


# ---- 1. parent pre-creates + head moves before dispatch returns --------

def test_parent_precreates_node_and_moves_head(monkeypatch, tmp_path):
    from openprogram.webui.routes import chat as routes_chat

    store = _store(tmp_path)
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: store)
    monkeypatch.setattr(
        "openprogram.webui.server._get_or_create_session",
        lambda sid=None, **k: {"id": sid or "s1", "last_workdirs": {}})
    monkeypatch.setattr(
        "openprogram.webui.server._default_agent_id", lambda: "main")
    monkeypatch.setattr(
        "openprogram.webui.server._is_run_active", lambda sid: False)
    monkeypatch.setattr(
        "openprogram.webui.server._emit_running_task_event", lambda *a, **k: None)
    monkeypatch.setattr(
        "openprogram.webui.ws_actions.session.broadcast_sessions_list",
        lambda *a, **k: None)

    class _Tool:
        name = "word_count"
        _is_agentic = True
    monkeypatch.setattr(
        "openprogram.functions.agent_tools", lambda names=None: [_Tool()])

    class _RM:
        def _enabled_model_keys(self):
            return ["k"]
    monkeypatch.setattr("openprogram.webui.server._runtime_management", _RM())

    captured = {}

    def _stop_dispatch(**kw):
        captured["anchor"] = kw.get("anchor_msg_id")
        return {"runtime_msg_id": None, "ok": True}
    monkeypatch.setattr(
        "openprogram.agent.dispatcher.dispatch_forced_tool_call", _stop_dispatch)

    # Run the dispatch thread inline so the assertions see a finished _run.
    def _inline_thread(target=None, args=(), kwargs=None, daemon=None):
        class _T:
            def start(_s):
                try:
                    target(*(args or ()), **(kwargs or {}))
                except Exception:
                    pass
            def is_alive(_s):
                return False
        return _T()
    monkeypatch.setattr(threading, "Thread", _inline_thread)

    res = routes_chat.run_agentic_function_call("word_count", {"text": "hi"}, "s1")
    assert "error" not in res

    # A top-level code node exists on disk and HEAD points at it.
    nodes = store.get_nodes("s1")
    tops = [n for n in nodes if n.is_code() and n.name == "word_count"]
    assert len(tops) == 1
    node = tops[0]
    assert (node.metadata or {}).get("status") == "running"
    assert node.input == {"text": "hi"}
    assert _head(store) == node.id

    # The child received the pre-created id as a ``|node:<id>`` anchor
    # suffix so its wrapper reuses it instead of appending a second node.
    assert captured["anchor"] == f"|node:{node.id}"


# ---- 2. child reuse: wrapper with _forced_node_id set does not dupe -----

def test_child_reuse_leaves_single_node_and_finalizes(tmp_path):
    from openprogram.agentic_programming.function import (
        agentic_function, _forced_node_id, create_pending_call_node,
    )

    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", "main", title="t")
    shim = GraphStoreShim(store, "s1")

    # Parent pre-creates the top-level node.
    nid = "abc123abc123"
    node = create_pending_call_node(
        pending_id=nid, function_name="wc", arguments={"text": "hi"},
        expose="io", caller="", forced_predecessor=None, store=shim,
    )
    shim.append(node)
    assert _head(store) == nid

    @agentic_function
    def wc(text):
        return len(text.split())

    # Simulate the child: _store installed + _forced_node_id set (as
    # runtime_attach would after decoding the anchor). No real spawn.
    store_token = _store_var.set(shim)
    node_token = _forced_node_id.set(nid)
    try:
        out = wc("hi there")
    finally:
        _forced_node_id.reset(node_token)
        _store_var.reset(store_token)
    assert out == 2

    # Still exactly one top-level node, reusing the pre-created id, now
    # finalized (status completed, output filled) by the wrapper's exit.
    nodes = store.get_nodes("s1")
    tops = [n for n in nodes if n.is_code() and n.name == "wc" and not n.caller]
    assert len(tops) == 1
    assert tops[0].id == nid
    assert (tops[0].metadata or {}).get("status") == "completed"
    assert tops[0].output == 2


# ---- in-process run with NO forced id: single node, no leftover --------

def test_in_process_run_without_forced_id_single_node(tmp_path):
    from openprogram.agentic_programming.function import agentic_function

    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", "main", title="t")
    shim = GraphStoreShim(store, "s1")

    @agentic_function
    def wc2(text):
        return len(text)

    store_token = _store_var.set(shim)
    try:
        wc2("abc")
    finally:
        _store_var.reset(store_token)

    tops = [n for n in store.get_nodes("s1")
            if n.is_code() and n.name == "wc2" and not n.caller]
    assert len(tops) == 1
    assert (tops[0].metadata or {}).get("status") == "completed"


# ---- 3. helper builds the same shape the wrapper writes ----------------

def test_create_pending_call_node_matches_wrapper_shape(tmp_path):
    from openprogram.agentic_programming.function import create_pending_call_node

    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", "main", title="t")
    shim = GraphStoreShim(store, "s1")

    node = create_pending_call_node(
        pending_id="nid1", function_name="fn", arguments={"a": 1},
        expose="io", caller="", forced_predecessor="fork-point", store=shim,
    )
    assert node.id == "nid1"
    assert node.role == "code"
    assert node.name == "fn"
    assert node.input == {"a": 1}
    assert node.output is None
    assert node.caller == ""
    assert node.metadata["status"] == "running"
    assert node.metadata["expose"] == "io"
    assert node.metadata["predecessor"] == "fork-point"

    # expose='hidden' → no node at all (matches the wrapper's no-op).
    assert create_pending_call_node(
        pending_id="x", function_name="fn", arguments={},
        expose="hidden", store=shim) is None


# ---- 4. child error marks the pre-created running node -------------------

def test_child_error_marks_precreated_running_node(monkeypatch, tmp_path):
    from openprogram.agent.dispatcher import forced_tool
    from openprogram.agentic_programming.function import create_pending_call_node

    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", "main", title="t")
    shim = GraphStoreShim(store, "s1")
    node = create_pending_call_node(
        pending_id="stuck1", function_name="wc", arguments={"text": "hi"},
        expose="io", caller="", store=shim)
    shim.append(node)

    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: store)

    class _Tool:
        name = "wc"
        _is_agentic = True
    monkeypatch.setattr(
        "openprogram.functions.agent_tools", lambda names=None: [_Tool()])
    # Child crashes before its wrapper could finalize → returns an error.
    monkeypatch.setattr(
        "openprogram.agent.process_runner.run_agentic_in_subprocess",
        lambda **kw: {"error": "kwargs pickle failed"})
    monkeypatch.setattr(
        "openprogram.webui._pause_stop.set_current_session_id", lambda sid: None)
    monkeypatch.setattr(
        "openprogram.webui._pause_stop.reset_current_session_id", lambda t: None)
    monkeypatch.setattr(
        "openprogram.webui._pause_stop.clear_cancel", lambda sid: None)

    out = forced_tool.dispatch_forced_tool_call(
        session_id="s1", anchor_msg_id="|node:stuck1", tool_name="wc",
        tool_input={"text": "hi"})
    assert out["ok"] is False
    assert "pickle" in out["error"]

    # The pre-created node no longer spins — flipped to error.
    node2 = next(n for n in store.get_nodes("s1") if n.id == "stuck1")
    assert (node2.metadata or {}).get("status") == "error"
