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
  4. child errors are reported to the canonical Agent driver; the forced
     leaf does not own execution lifecycle state.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from openprogram.store import SessionNodeWriter, _store as _store_var
from openprogram.store.session.session_store import SessionStore


def _store(tmp_path) -> SessionStore:
    s = SessionStore(tmp_path / "sessions-git")
    s.create_session("s1", "main", title="t")
    return s


def _head(store) -> str | None:
    return (store.get_session("s1") or {}).get("head_id")


# ---- 1. parent pre-creates + head moves before dispatch returns --------

def test_parent_threads_canonical_id_with_or_without_precreate(monkeypatch, tmp_path):
    from openprogram.webui.routes import chat as routes_chat
    from openprogram.webui import server as web_server

    with web_server._running_tasks_lock:
        web_server._running_tasks.pop("s1", None)
    web_server._unregister_active_runtime("s1")
    store = _store(tmp_path)
    real_is_run_active = web_server._is_run_active
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: store)
    monkeypatch.setattr(
        "openprogram.webui.server._get_or_create_session",
        lambda sid=None, **k: {"id": sid or "s1"})
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
        "openprogram.programs.agent_tools", lambda names=None: [_Tool()])
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _Tool() if name == _Tool.name else None,
    )

    class _RM:
        def _enabled_model_keys(self):
            return ["k"]

        def _resolve_session_provider_model(self, conv):
            assert conv["id"] == "s1"
            return "minimax-cn-coding-plan", "MiniMax-M3"
    monkeypatch.setattr("openprogram.webui.server._runtime_management", _RM())

    captured = {}

    def _stop_dispatch(**kw):
        captured["anchor"] = kw.get("anchor_msg_id")
        captured["execution_id"] = kw.get("execution_id")
        captured["record_exists"] = store.message_exists(
            "s1", kw.get("execution_id") or "",
        )
        captured["run_active"] = real_is_run_active("s1")
        captured["provider"] = kw.get("provider")
        captured["model"] = kw.get("model")
        captured["surface_context_snapshot"] = kw.get(
            "surface_context_snapshot"
        )
        return {"runtime_msg_id": None, "ok": True}

    monkeypatch.setattr(
        "openprogram.agent.dispatcher.dispatch_forced_tool_call", _stop_dispatch,
    )

    from openprogram.agent import production_driver
    from openprogram.execution.model import ExecutionStatus
    real_adapter = production_driver.CanonicalAgentAdapter

    class _Adapter:
        def __init__(self, *args, **kwargs):
            self._real = real_adapter(*args, **kwargs)

        def admit_payload(self, **kwargs):
            payload = kwargs["payload"]
            captured["anchor"] = payload.get("anchor_msg_id")
            captured["provider"] = payload.get("provider")
            captured["model"] = payload.get("model")
            captured["surface_context_snapshot"] = payload.get(
                "surface_context_snapshot"
            )
            admission = self._real.admit_payload(**kwargs)
            captured["execution_id"] = admission.execution_id
            captured["record_exists"] = store.message_exists(
                "s1", admission.execution_id,
            )
            captured["run_active"] = real_is_run_active("s1")
            return admission

        async def activate(self, admission, *, on_activated=None):
            service = self._real.driver._control_service()
            attempt, leased = service.attempts.lease(
                admission.execution_id,
                expected_version=admission.status_version,
                owner_id="unit-test",
                ttl_seconds=30,
            )
            active, running = service.attempts.activate(
                attempt.attempt_id,
                generation=attempt.generation,
                expected_execution_version=leased.status_version,
            )
            payload = self._real.store.get_agent_turn_input(
                admission.execution_id,
            ) or {}
            try:
                from openprogram.agent.dispatcher import dispatch_forced_tool_call

                result = dispatch_forced_tool_call(
                    session_id=admission.session_id,
                    anchor_msg_id=str(payload.get("anchor_msg_id") or ""),
                    tool_name=str(payload.get("tool_name") or ""),
                    tool_input=dict(payload.get("tool_input") or {}),
                    work_dir=payload.get("work_dir"),
                    agent_id=str(payload.get("agent_id") or "main"),
                    source=str(payload.get("source") or "web"),
                    provider=payload.get("provider"),
                    model=payload.get("model"),
                    response_format=payload.get("response_format"),
                    execution_id=admission.execution_id,
                    attempt_id=active.attempt_id,
                    generation=active.generation,
                    cancel_event=threading.Event(),
                    surface_context_snapshot=payload.get(
                        "surface_context_snapshot"
                    ),
                )
                target = ExecutionStatus.COMPLETED
                outcome = "completed"
            except BaseException as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
                target = ExecutionStatus.FAILED
                outcome = "failed"
            service.finish_attempt(
                attempt_id=active.attempt_id,
                generation=active.generation,
                expected_execution_version=running.status_version,
                target=target,
                outcome=outcome,
            )
            activation = SimpleNamespace(
                admission=admission, status_version=running.status_version,
            )
            if on_activated is not None:
                on_activated(activation)
            return activation, result

        def fail_admission(self, *args, **kwargs):
            return self._real.fail_admission(*args, **kwargs)

    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", _Adapter)

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
    monkeypatch.setattr(
        routes_chat, "threading", SimpleNamespace(Thread=_inline_thread)
    )

    res = routes_chat.run_agentic_function_call(
        "word_count",
        {"text": "hi"},
        "s1",
        origin_window_id="window-2",
        surface_ref={
            "version": 1,
            "window_id": "window-2",
            "tab_id": "tab-submitted",
            "access": "enabled",
        },
    )
    assert "error" not in res

    # A top-level code node exists on disk and HEAD points at it.
    nodes = store.get_nodes("s1")
    tops = [n for n in nodes if n.is_code() and n.name == "word_count"]
    assert len(tops) == 1
    node = tops[0]
    assert (node.metadata or {}).get("status") == "running"
    assert node.input == {"text": "hi"}
    assert node.metadata["surface_origin"] == {
        "version": 1,
        "window_id": "window-2",
        "tab_id": "tab-submitted",
    }
    assert set(node.metadata["surface_origin"]) == {
        "version", "window_id", "tab_id",
    }
    assert _head(store) == node.id

    # The child received the pre-created id as a ``|node:<id>`` anchor
    # suffix so its wrapper reuses it instead of appending a second node.
    assert captured["anchor"] == f"|node:{node.id}"
    assert captured["execution_id"].startswith("exec_")
    assert captured["execution_id"] != node.id
    assert captured["record_exists"] is False
    assert captured["run_active"] is True
    assert captured["provider"] == "minimax-cn-coding-plan"
    assert captured["model"] == "MiniMax-M3"
    assert captured["surface_context_snapshot"]["origin_window_id"] == "window-2"
    assert captured["surface_context_snapshot"]["origin_tab_id"] == "tab-submitted"

    res = routes_chat.run_agentic_function_call(
        "word_count",
        {"text": "window only"},
        "s1",
        origin_window_id="window-2",
        surface_ref={"version": 1, "window_id": "window-2"},
    )
    assert "error" not in res
    window_only = next(
        item for item in store.get_nodes("s1")
        if item.input == {"text": "window only"}
    )
    assert window_only.metadata["surface_origin"] == {
        "version": 1,
        "window_id": "window-2",
    }

    from openprogram.agentic_programming import function as function_module

    hidden = SimpleNamespace(
        expose="hidden",
        tool_name="hidden_probe",
        render_range=None,
        _fn=lambda: None,
    )
    monkeypatch.setitem(function_module._registry, "hidden_probe", hidden)

    class _HiddenTool:
        name = "hidden_probe"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs.agent_tools",
        lambda names=None: [_HiddenTool()],
    )
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _HiddenTool() if name == _HiddenTool.name else None,
    )
    captured.clear()

    res = routes_chat.run_agentic_function_call(
        "hidden_probe", {"secret": "do-not-persist"}, "s1",
    )

    hidden_id = captured["execution_id"]
    assert res["execution_id"] == hidden_id
    assert hidden_id.startswith("exec_")
    assert captured["record_exists"] is False
    from openprogram.execution import default_store
    assert default_store().get_execution(hidden_id) is not None
    hidden_node = next(
        n for n in store.get_nodes("s1")
        if (n.metadata or {}).get("expose") == "hidden"
    )
    assert hidden_node.id != hidden_id
    assert hidden_node.input in (None, {})
    assert "do-not-persist" not in str(hidden_node)
    assert "do-not-persist" not in (store.get_session("s1") or {}).get("title", "")
    assert "do-not-persist" not in str(store.get_messages("s1"))
    assert hidden_node.metadata["expose"] == "hidden"
    from openprogram.webui.graph_builder import build_session_graph
    from openprogram.webui._exec_dag import build_exec_dag_by_id
    graph = build_session_graph("s1")
    assert all(row["id"] != hidden_id for row in graph)
    assert "do-not-persist" not in str(graph)
    assert build_exec_dag_by_id("s1", hidden_id) is None
    assert default_store().get_execution(hidden_id).status.value == "completed"

    monkeypatch.setattr(
        "openprogram.programs.agent_tools", lambda names=None: [_Tool()])
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _Tool() if name == _Tool.name else None,
    )

    original_precreate = function_module.create_pending_call_node
    attempts = 0

    def _fail_precreate_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("pre-create failed")
        return original_precreate(**kwargs)

    monkeypatch.setattr(
        "openprogram.agentic_programming.function.create_pending_call_node",
        _fail_precreate_once,
    )
    captured.clear()

    res = routes_chat.run_agentic_function_call(
        "word_count", {"text": "again"}, "s1",
    )
    assert "error" not in res
    assert captured["execution_id"]
    assert captured["anchor"].startswith("|node:")
    assert not captured["anchor"].endswith(captured["execution_id"])
    assert captured["record_exists"] is False

    class _StartFailureThread:
        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(
        routes_chat,
        "threading",
        SimpleNamespace(Thread=lambda **_kwargs: _StartFailureThread()),
    )
    captured.clear()

    res = routes_chat.run_agentic_function_call(
        "word_count", {"text": "thread cannot start"}, "s1",
    )
    assert res["code"] == "function_start_failed"
    assert res["status_code"] == 500
    assert captured["execution_id"].startswith("exec_")
    from openprogram.execution import default_store as _execution_store
    assert _execution_store().get_execution(
        captured["execution_id"]
    ).status.value == "failed"
    from openprogram.webui import server as _server
    with _server._running_tasks_lock:
        assert "s1" not in _server._running_tasks
    failed_node = next(
        node for node in store.get_nodes("s1")
        if node.input == {"text": "thread cannot start"}
    )
    # Admission succeeds before thread startup. A startup failure closes the
    # parent-created DAG projection instead of leaving a permanent running
    # node; the execution lifecycle is still finalized by AgentDriver.
    assert failed_node.metadata["status"] == "failed"

    monkeypatch.setattr(
        routes_chat, "threading", SimpleNamespace(Thread=_inline_thread),
    )
    def _fail_dispatch(*_args, **_kwargs):
        raise ImportError("dispatcher unavailable")

    monkeypatch.setattr(
        "openprogram.agent.dispatcher.dispatch_forced_tool_call",
        _fail_dispatch,
    )
    captured.clear()
    res = routes_chat.run_agentic_function_call(
        "word_count", {"text": "dispatcher cannot import"}, "s1",
    )

    assert "error" not in res
    assert captured["execution_id"].startswith("exec_")
    assert _execution_store().get_execution(
        captured["execution_id"]
    ).status.value == "failed"
    with _server._running_tasks_lock:
        assert "s1" not in _server._running_tasks
    import_failed_node = next(
        node for node in store.get_nodes("s1")
        if node.input == {"text": "dispatcher cannot import"}
    )
    assert import_failed_node.metadata["status"] == "failed"

    def _always_fail_precreate(**kwargs):
        raise RuntimeError("persistent pre-create failure")

    monkeypatch.setattr(
        "openprogram.agentic_programming.function.create_pending_call_node",
        _always_fail_precreate,
    )
    captured.clear()

    res = routes_chat.run_agentic_function_call(
        "word_count", {"text": "never starts"}, "s1",
    )
    assert res["code"] == "execution_record_failed"
    assert captured == {}
    from openprogram.webui import server as _server
    with _server._running_tasks_lock:
        assert "s1" not in _server._running_tasks
    _server._unregister_active_runtime("s1")


def test_missing_session_model_refuses_before_dispatch(monkeypatch):
    from openprogram.webui.routes import chat as routes_chat

    monkeypatch.setattr(
        "openprogram.webui.server._get_or_create_session",
        lambda sid=None, **kwargs: {"id": sid or "s1"},
    )
    monkeypatch.setattr(
        "openprogram.webui.server._is_run_active",
        lambda session_id: False,
    )

    class Tool:
        name = "word_count"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs.agent_tools",
        lambda names=None: [Tool()],
    )
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: Tool() if name == Tool.name else None,
    )

    class RuntimeManagement:
        def _enabled_model_keys(self):
            return [("some-provider", "some-model")]

        def _resolve_session_provider_model(self, conv):
            return None, None

    monkeypatch.setattr(
        "openprogram.webui.server._runtime_management",
        RuntimeManagement(),
    )
    dispatched = []
    monkeypatch.setattr(
        "openprogram.agent.dispatcher.dispatch_forced_tool_call",
        lambda **kwargs: dispatched.append(kwargs),
    )

    result = routes_chat.run_agentic_function_call(
        "word_count", {}, "s1",
    )
    assert result["status_code"] == 409
    assert result["code"] == "no_model"
    assert dispatched == []
    from openprogram.webui import server as web_server
    with web_server._running_tasks_lock:
        assert "s1" not in web_server._running_tasks


# ---- 2. child reuse: wrapper with _forced_node_id set does not dupe -----

def test_child_reuse_leaves_single_node_and_finalizes(tmp_path):
    from openprogram.agentic_programming.function import (
        agentic_function, _forced_node_id, create_pending_call_node,
    )

    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", "main", title="t")
    shim = SessionNodeWriter(store, "s1")

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
    shim = SessionNodeWriter(store, "s1")

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
    shim = SessionNodeWriter(store, "s1")

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
    assert node.predecessor == "fork-point"

    # expose='hidden' → no node at all (matches the wrapper's no-op).
    assert create_pending_call_node(
        pending_id="x", function_name="fn", arguments={},
        expose="hidden", store=shim) is None


# ---- 4. child error is reported to the canonical driver ------------------

def test_child_error_marks_precreated_running_node(monkeypatch, tmp_path):
    from openprogram.agent.dispatcher import forced_tool
    from openprogram.agentic_programming.function import create_pending_call_node

    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", "main", title="t")
    shim = SessionNodeWriter(store, "s1")
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
        "openprogram.programs.agent_tools", lambda names=None: [_Tool()])
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _Tool() if name == _Tool.name else None,
    )
    # Child crashes before its wrapper could finalize → returns an error.
    monkeypatch.setattr(
        "openprogram.agent.process_runner.run_agentic_in_subprocess",
        lambda **kw: {"error": "kwargs pickle failed"})
    monkeypatch.setattr(
        "openprogram.agent.run_control.set_current_session_id", lambda sid: None)
    monkeypatch.setattr(
        "openprogram.agent.run_control.reset_current_session_id", lambda t: None)
    monkeypatch.setattr(
        "openprogram.agent.run_control.clear_cancel", lambda sid: None)

    out = forced_tool.dispatch_forced_tool_call(
        session_id="s1", anchor_msg_id="|node:stuck1", tool_name="wc",
        tool_input={"text": "hi"})
    assert out["ok"] is False
    assert "pickle" in out["error"]

    # The forced-tool leaf does not own lifecycle state; AgentDriver performs
    # the terminal transition after receiving this result.
    node2 = next(n for n in store.get_nodes("s1") if n.id == "stuck1")
    assert (node2.metadata or {}).get("status") == "running"
    assert node2.output is None


@pytest.mark.parametrize(
    ("initial_status", "subprocess_out", "expected_status"),
    [
        ("running", {}, "completed"),
        ("running", {"error": "child failed"}, "error"),
        ("running", {"killed": True}, "interrupted"),
        ("cancelling", {"killed": True}, "cancelled"),
        ("completed", {"error": "late failure"}, "completed"),
        ("error", {}, "error"),
        ("interrupted", {}, "interrupted"),
        ("cancelled", {}, "cancelled"),
    ],
)
def test_parent_page_cleanup_failure_preserves_terminal_metadata(
    monkeypatch, tmp_path, initial_status, subprocess_out, expected_status,
):
    from openprogram.agent.dispatcher import forced_tool
    from openprogram.agentic_programming.function import create_pending_call_node

    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", "main", title="t")
    shim = SessionNodeWriter(store, "s1")
    node = create_pending_call_node(
        pending_id="gui-cleanup",
        function_name="gui_agent",
        arguments={"task": "inspect", "surface": "browser"},
        expose="io",
        caller="",
        store=shim,
    )
    shim.append(node)
    original_result = {"status": "succeeded", "success": True}
    original_metadata = {"status": initial_status}
    if initial_status in {"cancelling", "cancelled"}:
        original_metadata["reason_code"] = "cancel.user"
    if initial_status == "error":
        original_metadata["error"] = "original failure"
    if initial_status in {"completed", "error", "interrupted", "cancelled"}:
        original_metadata["finished_at"] = 123.0
    shim.update(
        "gui-cleanup",
        output=original_result,
        metadata=original_metadata,
    )
    cleanup_result = {
        "status": "infeasible",
        "success": False,
        "infeasible_declared": True,
        "reason_code": "page_cleanup_failed",
        "summary": (
            "The agent-created background Page could not be confirmed closed."
        ),
        "handoff_instruction": "Close the remaining background Page.",
    }

    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: store,
    )

    class _Tool:
        name = "gui_agent"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *args, **kwargs: _Tool() if name == _Tool.name else None,
    )
    monkeypatch.setattr(
        "openprogram.agent.process_runner.run_agentic_in_subprocess",
        lambda **kwargs: {
            "page_cleanup_failed": True,
            "page_cleanup_result": cleanup_result,
            **subprocess_out,
        },
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.set_current_session_id", lambda _sid: None,
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.reset_current_session_id", lambda _token: None,
    )

    result = forced_tool.dispatch_forced_tool_call(
        session_id="s1",
        anchor_msg_id="|node:gui-cleanup",
        tool_name="gui_agent",
        tool_input={"task": "inspect", "surface": "browser"},
        surface_context_snapshot={"context_id": "ctx", "surfaces": []},
    )

    assert result["runtime_msg_id"] is None
    assert result["ok"] is False
    assert result["error"]
    assert result["page_cleanup_result"] == cleanup_result
    persisted = next(
        item for item in store.get_nodes("s1") if item.id == "gui-cleanup"
    )
    assert persisted.metadata["status"] == initial_status
    assert persisted.output == original_result
    if initial_status in {"cancelling", "cancelled"}:
        assert persisted.metadata["reason_code"] == "cancel.user"
    if initial_status == "error":
        assert persisted.metadata["error"] == "original failure"
    if initial_status in {"completed", "error", "interrupted", "cancelled"}:
        assert persisted.metadata["finished_at"] == 123.0
    else:
        assert "finished_at" not in persisted.metadata


def test_browser_surface_capture_error_defers_to_child_handoff(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.agent.dispatcher import forced_tool

    class _Tool:
        name = "gui_agent"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _Tool() if name == _Tool.name else None,
    )
    monkeypatch.setattr(
        surface_context,
        "capture_pages",
        lambda: (_ for _ in ()).throw(RuntimeError("Page capture unavailable")),
    )
    seen = {}
    monkeypatch.setattr(
        "openprogram.agent.process_runner.run_agentic_in_subprocess",
        lambda **kwargs: seen.update(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.set_current_session_id", lambda _sid: None,
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.reset_current_session_id", lambda _token: None,
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.clear_cancel", lambda _sid: None,
    )

    result = forced_tool.dispatch_forced_tool_call(
        session_id="s1",
        anchor_msg_id="|node:gui-agent",
        tool_name="gui_agent",
        tool_input={"task": "inspect", "surface": "browser"},
    )

    assert result["ok"] is True
    assert seen["surface_context_snapshot"]["surfaces"] == []
    assert seen["surface_context_snapshot"]["origin_window_id"] == ""
