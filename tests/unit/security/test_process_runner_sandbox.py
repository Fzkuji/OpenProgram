from __future__ import annotations

from openprogram.runtime.agent import Agent

from contextvars import copy_context
import inspect
import pickle
import queue
import threading
import time

import pytest


def test_spawn_payload_contains_explicit_sandbox_snapshot(monkeypatch):
    from openprogram.runtime.conversation import process_runner

    captured = {}

    class FakeProcess:
        exitcode = 0

        def __init__(self, *, target, args, daemon):
            captured["target"] = target
            captured["args"] = args

        def start(self):
            pass

        def join(self):
            pass

        def is_alive(self):
            return False

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            return FakeProcess(**kwargs)

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())
    monkeypatch.setattr(
        process_runner,
        "_capture_sandbox_snapshot",
        lambda: {"enabled": True, "policy": {"network": False}},
    )

    process_runner.run_agent_method_in_subprocess(
        tool_name="demo",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        work_dir="/workspace",
        provider="minimax-cn-coding-plan",
        model="MiniMax-M3",
    )

    payload = dict(zip(
        inspect.signature(process_runner._child_entry).parameters,
        captured["args"],
    ))
    assert payload["provider"] == "minimax-cn-coding-plan"
    assert payload["model"] == "MiniMax-M3"
    assert payload["sandbox_policy_snapshot"] == {
        "enabled": True,
        "policy": {"network": False},
    }
    assert payload["authority_snapshot"] is None


def test_spawn_payload_preserves_turn_render_range(monkeypatch):
    from openprogram.runtime.conversation import process_runner

    captured = {}

    class FakeProcess:
        exitcode = 0

        def __init__(self, *, target, args, daemon):
            captured["args"] = args

        def start(self):
            pass

        def join(self):
            pass

        def is_alive(self):
            return False

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            return FakeProcess(**kwargs)

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())

    process_runner.run_agent_method_in_subprocess(
        tool_name="demo",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        render_range={"callers": 0, "subcalls": 2},
    )

    payload = dict(zip(
        inspect.signature(process_runner._child_entry).parameters,
        captured["args"],
    ))
    assert payload["render_range"] == {"callers": 0, "subcalls": 2}


def test_subprocess_timeout_kills_the_process_tree(monkeypatch):
    from openprogram.runtime.conversation import process_runner

    joined = []
    killed = []

    class FakeProcess:
        pid = 4321
        exitcode = -9

        def __init__(self, **_kwargs):
            self.alive = True

        def start(self):
            pass

        def join(self, timeout=None):
            joined.append(timeout)

        def is_alive(self):
            return self.alive

        def kill(self):
            raise AssertionError("process-tree termination should succeed")

    process = FakeProcess()

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **_kwargs):
            return process

    def kill_process_tree(pid):
        killed.append(pid)
        process.alive = False
        return True

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())
    monkeypatch.setattr("openprogram.host._platform.processes.kill_process_tree", kill_process_tree)

    result = process_runner.run_agent_method_in_subprocess(
        tool_name="demo",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        timeout_seconds=2.5,
    )

    assert joined == [2.5, 5]
    assert killed == [4321]
    error = result.pop("error")
    progress = result.pop("progress")
    assert result == {"killed": True, "timed_out": True, "signal": 9}
    assert error.splitlines()[0] == "agentic subprocess timed out after 2.5 seconds"
    assert "demo was stopped at its 2.5-second limit." in error
    assert "reported no progress" in error
    assert progress["events"] == 0
    assert progress["steps"] == [] and progress["recent"] == []


def test_agentic_limits_hand_the_budget_to_the_agent(monkeypatch):
    from openprogram.runtime.conversation import process_runner

    monkeypatch.setattr(process_runner, "_BOUNDED_AGENTIC_TOOLS", {"bounded_agent"})
    monkeypatch.delenv("OPENPROGRAM_AGENTIC_TIMEOUT_S", raising=False)
    args = {"task": "inspect"}
    limits = process_runner.agentic_subprocess_limits("bounded_agent", args)
    assert limits.kwargs == {"task": "inspect", "max_seconds": 300.0}
    assert args == {"task": "inspect"}
    assert (limits.budget_seconds, limits.timeout_seconds) == (300.0, 360.0)

    explicit = process_runner.agentic_subprocess_limits(
        "bounded_agent", {"task": "t", "max_seconds": 12},
    )
    assert explicit.kwargs["max_seconds"] == 12
    assert (explicit.budget_seconds, explicit.timeout_seconds) == (12.0, 42.0)

    unbounded = process_runner.agentic_subprocess_limits(
        "bounded_agent", {"task": "t", "max_seconds": 0},
    )
    assert unbounded.kwargs == {"task": "t", "max_seconds": 0}
    assert (unbounded.budget_seconds, unbounded.timeout_seconds) == (None, None)

    other = process_runner.agentic_subprocess_limits("wc", {"path": "a"})
    assert other.kwargs == {"path": "a"}
    assert (other.budget_seconds, other.timeout_seconds) == (None, None)


@pytest.mark.parametrize(
    ("raw", "budget", "timeout"),
    [("900", 900.0, 1020.0), ("0", None, None), ("-1", None, None),
     ("", 300.0, 360.0), ("soon", 300.0, 360.0)],
)
def test_agentic_default_budget_comes_from_the_environment(
    monkeypatch, raw, budget, timeout,
):
    from openprogram.runtime.conversation import process_runner

    monkeypatch.setattr(process_runner, "_BOUNDED_AGENTIC_TOOLS", {"bounded_agent"})
    monkeypatch.setenv("OPENPROGRAM_AGENTIC_TIMEOUT_S", raw)
    limits = process_runner.agentic_subprocess_limits("bounded_agent", {"task": "t"})
    assert (limits.budget_seconds, limits.timeout_seconds) == (budget, timeout)
    assert limits.kwargs.get("max_seconds") == budget
    explicit = process_runner.agentic_subprocess_limits(
        "bounded_agent", {"task": "t", "max_seconds": 50},
    )
    assert explicit.budget_seconds == 50.0


def test_agentic_timeout_reports_the_childs_last_progress(monkeypatch):
    from openprogram.runtime.conversation import process_runner

    monkeypatch.setattr(process_runner, "_BOUNDED_AGENTIC_TOOLS", {"bounded_agent"})
    delivered = threading.Event()
    killed = []
    tree = {
        "path": "run", "name": "bounded_agent", "status": "running",
        "children": [
            {"path": "a", "name": "open_page", "status": "completed",
             "duration_ms": 120},
            {"path": "b", "name": "plan_next_capability", "status": "completed",
             "duration_ms": 4200},
            {"path": "c", "name": "computer_use", "status": "running",
             "children": [
                 {"path": "d", "name": "locate_target", "status": "error",
                  "error": "element not found\ntrace"},
             ]},
        ],
    }

    class FakeProcess:
        pid = 99
        exitcode = -9

        def __init__(self, *, target, args, daemon):
            del target, daemon
            self.event_queue = args[6]
            self.alive = True

        def start(self):
            self.event_queue.put({"type": "chat_response", "data": {
                "type": "tree_update", "session_id": "s", "tree": tree,
            }})
            self.event_queue.put({"type": "chat_response", "data": {
                "type": "execution_stream", "op": "block_started",
                "kind": "tool_ref", "tool_name": "computer_use",
            }})
            self.event_queue.put({"type": "marker"})

        def join(self, timeout=None):
            if timeout == 360.0:
                assert delivered.wait(30)

        def is_alive(self):
            return self.alive

    holder = {}

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            holder["process"] = FakeProcess(**kwargs)
            return holder["process"]

    def kill_process_tree(pid):
        killed.append(pid)
        holder["process"].alive = False
        return True

    def on_event(env):
        if env.get("type") == "marker":
            delivered.set()

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())
    monkeypatch.setattr("openprogram.host._platform.processes.kill_process_tree", kill_process_tree)

    result = process_runner.run_agent_method_in_subprocess(
        tool_name="bounded_agent",
        kwargs={"task": "t", "max_seconds": 300.0},
        session_id="s",
        anchor_msg_id="m",
        on_event=on_event,
        timeout_seconds=360.0,
        budget_seconds=300.0,
    )

    assert killed == [99]
    assert result["timed_out"] is True and result["killed"] is True
    progress = result["progress"]
    assert progress["events"] == 3
    assert progress["steps"] == [
        "open_page: completed (0.1 s)",
        "plan_next_capability: completed (4.2 s)",
        "computer_use: running",
        "  locate_target: error - element not found",
    ]
    assert progress["recent"][-1].endswith("tool computer_use started")
    error = result["error"]
    assert error.splitlines()[0] == "agentic subprocess timed out after 360 seconds"
    assert "within its 300-second budget plus 60 seconds" in error
    assert "  computer_use: running" in error
    assert "OPENPROGRAM_AGENTIC_TIMEOUT_S" in error
    assert "pass max_seconds to bounded_agent" in error


def test_blocked_non_page_event_does_not_report_page_cleanup_failure(monkeypatch):
    from openprogram.runtime.conversation import process_runner

    callback_started = threading.Event()
    release_callback = threading.Event()
    callback_finished = threading.Event()
    callback_threads = []

    class FakeProcess:
        exitcode = 0

        def __init__(self, *, target, args, daemon):
            del target, daemon
            self.result_path = args[5]
            self.event_queue = args[6]

        def start(self):
            with open(self.result_path, "wb") as stream:
                pickle.dump({"status": "succeeded", "success": True}, stream)
            self.event_queue.put({"type": "ordinary_progress"})

        def join(self, timeout=None):
            del timeout
            assert callback_started.wait(30)

        def is_alive(self):
            return False

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            return FakeProcess(**kwargs)

    def on_event(_event):
        callback_threads.append(threading.current_thread())
        callback_started.set()
        try:
            release_callback.wait(30)
        finally:
            callback_finished.set()

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())

    try:
        result = process_runner.run_agent_method_in_subprocess(
            tool_name="demo",
            kwargs={},
            session_id="s-event",
            anchor_msg_id="m",
            on_event=on_event,
        )
        # Verify the cleanup ordering, not sandbox setup or host scheduling time.
        assert callback_started.is_set()
        assert not callback_finished.is_set()
    finally:
        release_callback.set()
        for thread in callback_threads:
            thread.join(timeout=30)
            assert not thread.is_alive()
    assert result == {"status": "succeeded", "success": True}
    assert "page_cleanup_failed" not in result


@pytest.mark.parametrize(
    "close_results",
    [
        [False, False],
        [False, True],
    ],
)
def test_subprocess_timeout_reports_late_background_page_cleanup(
    monkeypatch, close_results,
):
    from openprogram.runtime.conversation import process_runner
    from openprogram.interfaces.server.ws_actions import webtab

    owner = object()
    open_started = threading.Event()
    release_open = threading.Event()
    close_attempted = threading.Event()
    closed_tabs = []

    class FakeProcess:
        pid = 4321
        exitcode = -9

        def __init__(self, *, target, args, daemon):
            del target, daemon
            self.alive = True
            self.event_queue = args[6]

        def start(self):
            self.event_queue.put({
                "__op_webtab__": True,
                "data": {
                    "req_id": "late-open",
                    "command": {
                        "op": "open",
                        "url": "https://www.google.com/",
                        "window_id": "window-1",
                        "background": True,
                    },
                    "timeout": 2,
                },
            })

        def join(self, timeout=None):
            if self.alive and timeout != 5:
                assert open_started.wait(30)

        def is_alive(self):
            return self.alive

        def kill(self):
            raise AssertionError("process-tree termination should succeed")

    process = FakeProcess

    class FakeContext:
        Queue = queue.Queue

        def Process(self, **kwargs):
            return process(**kwargs)

    def request_on_ws(_owner, command, timeout=5.0):
        del timeout
        if command["op"] == "open":
            open_started.set()
            assert release_open.wait(30)
            return {
                "ok": True,
                "created": True,
                "reused": False,
                "window_id": "window-1",
                "tab_id": "tab-late",
                "target_id": "target-late",
            }
        closed_tabs.append(command["tab_id"])
        close_attempted.set()
        succeeded = close_results[min(len(closed_tabs) - 1, len(close_results) - 1)]
        return {"ok": succeeded, **({} if succeeded else {
            "error": "close rejected",
        })}

    monkeypatch.setattr(process_runner.mp, "get_context", lambda _kind: FakeContext())

    def kill_process_tree(_pid):
        process_runner._active["s"].alive = False
        release_open.set()
        return True

    monkeypatch.setattr(
        "openprogram.host._platform.processes.kill_process_tree",
        kill_process_tree,
    )
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )
    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    monkeypatch.setattr(webtab, "register_binding", lambda *_args, **_kwargs: "surface-late")
    monkeypatch.setattr(webtab, "binding_connection", lambda _binding_id: owner)
    monkeypatch.setattr(webtab, "binding_page_key", lambda _binding_id: "page:late")
    monkeypatch.setattr(webtab, "binding_revisions", lambda _binding_id: {})
    monkeypatch.setattr(webtab, "release_binding", lambda _binding_id: None)

    started_at = time.monotonic()
    result = process_runner.run_agent_method_in_subprocess(
        tool_name="browser_agent",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        timeout_seconds=0.1,
        surface_context_snapshot={"origin_window_id": "window-1"},
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 2.0
    assert close_attempted.is_set()
    assert closed_tabs == ["tab-late", "tab-late"]
    if close_results[-1]:
        assert result["timed_out"] is True
        assert "page_cleanup_failed" not in result
    else:
        assert result["reason_code"] == "page_cleanup_failed"
        assert result["success"] is False
        assert result["infeasible_declared"] is True
        assert result["page_cleanup_failed"] is True
        assert result["page_cleanup_result"]["reason_code"] == (
            "page_cleanup_failed"
        )
        assert result["page_cleanup_result"]["cleanup_failures"][0][
            "tab_id"
        ] == "tab-late"
        assert "Close the remaining background Page" in result[
            "handoff_instruction"
        ]


def test_child_entry_keeps_the_legacy_positional_payload_layout():
    from openprogram.runtime.conversation import process_runner

    old_payload = tuple(object() for _ in range(15))
    bound = inspect.signature(process_runner._child_entry).bind(*old_payload)
    assert bound.arguments["render_range"] is old_payload[11]
    assert bound.arguments["usage_ctx_snapshot"] is old_payload[12]
    assert bound.arguments["sandbox_policy_snapshot"] is old_payload[13]
    assert bound.arguments["authority_snapshot"] is old_payload[14]


def test_child_entry_builds_the_session_selected_custom_runtime(
    monkeypatch, tmp_path,
):
    from openprogram.runtime.conversation import process_runner
    from openprogram.state.store import _current_turn_id, _store
    from openprogram.runtime.agent.call_state import _current_runtime
    from openprogram.state.store.session.session_store import SessionStore
    import openprogram.runtime.context.session_db as session_db
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.programs as programs
    import openprogram.models.providers.settings.config_read as config_read
    import openprogram.models.providers.catalog.enabled_models as enabled_models
    import openprogram.models.providers.catalog.models as provider_models

    config = {"minimax-cn-coding-plan": {"models": [
        {"id": "MiniMax-M3", "name": "MiniMax M3"},
    ]}}
    monkeypatch.setattr(config_read, "read_providers_config", lambda: config)
    registry = enabled_models._load()
    monkeypatch.setattr(enabled_models, "ENABLED_MODELS", registry)
    monkeypatch.setattr(provider_models, "ENABLED_MODELS", registry)

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main", title="test")
    monkeypatch.setattr(session_db, "default_db", lambda: store)
    monkeypatch.setattr(programs, "agent_tools", lambda names=None: [])
    monkeypatch.setattr(execution_run_control, "set_current_session_id", lambda sid: None)
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "0")

    result_path = tmp_path / "result.pkl"
    previous = (
        _store.get(None),
        _current_turn_id.get(None),
        _current_runtime.get(None),
    )
    try:
        copy_context().run(
            process_runner._child_entry,
            "missing_probe",
            {},
            "s",
            "ROOT",
            None,
            str(result_path),
            queue.Queue(),
            provider="minimax-cn-coding-plan",
            model="MiniMax-M3",
        )
    finally:
        _store.set(previous[0])
        _current_turn_id.set(previous[1])
        _current_runtime.set(previous[2])

    with result_path.open("rb") as handle:
        result = pickle.load(handle)
    assert result == {"error": "tool not found: missing_probe"}


@pytest.mark.parametrize("tool_failed", [False, True])
def test_child_entry_force_invokes_hidden_agentic_tool(monkeypatch, tmp_path, tool_failed):
    from types import SimpleNamespace

    from openprogram.runtime.conversation import process_runner
    from openprogram.runtime.agent.call_state import (

        _current_runtime,
    )
    from openprogram.state.store import _current_turn_id, _store
    from openprogram.state.store.session.session_store import SessionStore
    import openprogram.runtime.conversation.turn_runtime as dispatcher
    from openprogram.runtime.execution import run_control as execution_run_control
    import openprogram.runtime.context.session_db as session_db
    import openprogram.programs as programs
    import openprogram.models.providers.settings.config_read as config_read
    import openprogram.models.providers.catalog.enabled_models as enabled_models
    import openprogram.models.providers.catalog.models as provider_models

    class _HiddenChildProbeAgent(Agent):
        method_options = {'hidden_child_probe': {'tool_visible': False, 'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'hidden_child_probe'}}

        def hidden_child_probe(self, ):
            return "ran"

    _HiddenChildProbeAgent().hidden_child_probe

    from openprogram.programs import agent_tools
    assert agent_tools(names=["hidden_child_probe"]) == []

    config = {"minimax-cn-coding-plan": {"models": [
        {"id": "MiniMax-M3", "name": "MiniMax M3"},
    ]}}
    monkeypatch.setattr(config_read, "read_providers_config", lambda: config)
    registry = enabled_models._load()
    monkeypatch.setattr(enabled_models, "ENABLED_MODELS", registry)
    monkeypatch.setattr(provider_models, "ENABLED_MODELS", registry)

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main", title="test")
    monkeypatch.setattr(session_db, "default_db", lambda: store)
    monkeypatch.setattr(programs, "agent_tools", lambda names=None: [])
    monkeypatch.setattr(execution_run_control, "set_current_session_id", lambda sid: None)
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "0")

    class DummyResult:
        content = [SimpleNamespace(text="browser unavailable" if tool_failed else "ran")]
        is_error = tool_failed

    class DummyWrapped:
        async def execute(self, *a, **k):
            return DummyResult()

    seen = {}

    def _wrap(tool, *a, **k):
        seen["name"] = tool.name
        return DummyWrapped()

    monkeypatch.setattr(dispatcher, "_wrap_agentic_runtime_block", _wrap)

    result_path = tmp_path / "result.pkl"
    previous = (
        _store.get(None),
        _current_turn_id.get(None),
        _current_runtime.get(None),
    )
    try:
        copy_context().run(
            process_runner._child_entry,
            "hidden_child_probe",
            {},
            "s",
            "ROOT",
            None,
            str(result_path),
            queue.Queue(),
            provider="minimax-cn-coding-plan",
            model="MiniMax-M3",
        )
    finally:
        _store.set(previous[0])
        _current_turn_id.set(previous[1])
        _current_runtime.set(previous[2])

    with result_path.open("rb") as handle:
        result = pickle.load(handle)
    assert seen.get("name") == "hidden_child_probe"
    assert result.get("ok") is (not tool_failed)
    assert result.get("text") == ("browser unavailable" if tool_failed else "ran")
    if tool_failed:
        assert result["error"] == "browser unavailable"
