from __future__ import annotations

import inspect
import pickle
import queue
import threading
import time

import pytest


def test_spawn_payload_contains_explicit_sandbox_snapshot(monkeypatch):
    from openprogram.agent import process_runner

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

    process_runner.run_agentic_in_subprocess(
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
    from openprogram.agent import process_runner

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

    process_runner.run_agentic_in_subprocess(
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
    from openprogram.agent import process_runner

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
    monkeypatch.setattr("openprogram._compat.kill_process_tree", kill_process_tree)

    result = process_runner.run_agentic_in_subprocess(
        tool_name="demo",
        kwargs={},
        session_id="s",
        anchor_msg_id="m",
        timeout_seconds=2.5,
    )

    assert joined == [2.5, 5]
    assert killed == [4321]
    assert result == {
        "error": "agentic subprocess timed out after 2.5 seconds",
        "killed": True,
        "timed_out": True,
        "signal": 9,
    }


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
    from openprogram.agent import process_runner
    from openprogram.webui.ws_actions import webtab

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
                assert open_started.wait(1)

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
            assert release_open.wait(5)
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
    monkeypatch.setattr(
        "openprogram._compat.kill_process_tree",
        lambda _pid: setattr(process_runner._active["s"], "alive", False) or True,
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
    try:
        result = process_runner.run_agentic_in_subprocess(
            tool_name="gui_agent",
            kwargs={},
            session_id="s",
            anchor_msg_id="m",
            timeout_seconds=0.1,
            surface_context_snapshot={"origin_window_id": "window-1"},
        )
    finally:
        elapsed = time.monotonic() - started_at
        release_open.set()

    assert elapsed < 2.0
    assert close_attempted.wait(2)
    assert closed_tabs == ["tab-late", "tab-late"]
    assert result["reason_code"] == "page_cleanup_failed"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["page_cleanup_failed"] is True
    assert result["page_cleanup_result"]["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]


def test_child_entry_keeps_the_legacy_positional_payload_layout():
    from openprogram.agent import process_runner

    old_payload = tuple(object() for _ in range(15))
    bound = inspect.signature(process_runner._child_entry).bind(*old_payload)
    assert bound.arguments["render_range"] is old_payload[11]
    assert bound.arguments["usage_ctx_snapshot"] is old_payload[12]
    assert bound.arguments["sandbox_policy_snapshot"] is old_payload[13]
    assert bound.arguments["authority_snapshot"] is old_payload[14]


def test_child_entry_builds_the_session_selected_custom_runtime(
    monkeypatch, tmp_path,
):
    from openprogram.agent import process_runner
    from openprogram.store import _current_turn_id, _store
    from openprogram.agentic_programming.function import _current_runtime
    from openprogram.store.session.session_store import SessionStore
    import openprogram.agent.session_db as session_db
    import openprogram.agent.run_control as run_control
    import openprogram.programs as programs
    import openprogram.providers._config_read as config_read
    import openprogram.providers.enabled_models as enabled_models
    import openprogram.providers.models as provider_models

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
    monkeypatch.setattr(run_control, "set_current_session_id", lambda sid: None)
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "0")

    result_path = tmp_path / "result.pkl"
    previous = (
        _store.get(None),
        _current_turn_id.get(None),
        _current_runtime.get(None),
    )
    try:
        process_runner._child_entry(
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


def test_child_entry_force_invokes_hidden_agentic_tool(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from openprogram.agent import process_runner
    from openprogram.agentic_programming.function import (
        agentic_function,
        _current_runtime,
    )
    from openprogram.store import _current_turn_id, _store
    from openprogram.store.session.session_store import SessionStore
    import openprogram.agent.dispatcher as dispatcher
    import openprogram.agent.session_db as session_db
    import openprogram.agent.run_control as run_control
    import openprogram.programs as programs
    import openprogram.providers._config_read as config_read
    import openprogram.providers.enabled_models as enabled_models
    import openprogram.providers.models as provider_models

    @agentic_function(tool_visible=False)
    def hidden_child_probe():
        return "ran"

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
    monkeypatch.setattr(run_control, "set_current_session_id", lambda sid: None)
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "0")

    class DummyResult:
        content = [SimpleNamespace(text="ran")]

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
        process_runner._child_entry(
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
    assert result.get("ok") is True
    assert result.get("text") == "ran"
