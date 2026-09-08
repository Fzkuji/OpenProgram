"""Large tool outputs are loaded lazily over the session WebSocket."""

from __future__ import annotations

import asyncio
import json

import pytest

from openprogram.store import SessionStore
from openprogram.webui.ws_actions import session as ws_session


class FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


@pytest.fixture
def session_with_tool_outputs(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    store.create_session("session-1", "main")
    large = 'x"\\\n' * (ws_session.TOOL_OUTPUT_INLINE_MAX_BYTES // 2)
    store.append_message("session-1", {
        "id": "user-1", "role": "user", "content": "inspect",
    })
    store.append_message("session-1", {
        "id": "assistant-1", "role": "assistant", "content": "done",
        "predecessor": "user-1",
        "extra": json.dumps({"blocks": [{
            "type": "tool",
            "tool": "read",
            "tool_call_id": "tool-large",
            "input": '{"path": "x"}',
            "result": large,
            "is_error": False,
        }]}),
    })
    for node_id, result in (("tool-large", large), ("tool-small", "small")):
        store.append_message("session-1", {
            "id": node_id,
            "role": "tool",
            "content": result,
            "function": "read",
            "caller": "assistant-1",
            "extra": json.dumps({
                "tool_use": {"name": "read", "arguments": {"path": "x"}},
            }),
        })

    from openprogram.webui import server

    with server._sessions_lock:
        server._sessions["session-1"] = {"id": "session-1"}
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(server, "refresh_context_stats", lambda _sid: None)
    yield store, large
    with server._sessions_lock:
        server._sessions.pop("session-1", None)


def test_session_loaded_truncates_only_oversized_tool_output(
    session_with_tool_outputs,
):
    store, large = session_with_tool_outputs
    ws = FakeWS()

    asyncio.run(ws_session.handle_load_session(
        ws, {"session_id": "session-1"},
    ))

    loaded = next(frame for frame in ws.frames if frame["type"] == "session_loaded")
    assistant = next(
        message for message in loaded["data"]["messages"]
        if message["id"] == "assistant-1"
    )
    calls = {call["tool_call_id"]: call for call in assistant["tool_calls"]}
    truncated = calls["tool-large"]
    assert truncated["truncated"] is True
    assert truncated["total_bytes"] == len(
        json.dumps(large, ensure_ascii=False).encode("utf-8")
    )
    assert truncated["message_id"] == "assistant-1"
    assert truncated["node_id"] == "tool-large"
    assert large.startswith(truncated["result"])
    assert len(json.dumps(
        truncated["result"], ensure_ascii=False,
    ).encode("utf-8")) <= ws_session.TOOL_OUTPUT_INLINE_MAX_BYTES
    assert calls["tool-small"] == {
        "tool_call_id": "tool-small",
        "tool": "read",
        "input": '{"path": "x"}',
        "result": "small",
        "is_error": False,
    }
    block = assistant["blocks"][0]
    assert block["truncated"] is True
    assert block["total_bytes"] == truncated["total_bytes"]
    assert block["message_id"] == "assistant-1"
    assert block["node_id"] == "tool-large"
    assert large.startswith(block["result"])
    persisted = {message["id"]: message for message in store.get_messages("session-1")}
    assert persisted["tool-large"]["content"] == large
    assert persisted["assistant-1"]["blocks"][0]["result"] == large


def test_session_load_repairs_stale_restart_marker_from_canonical_pause(
    session_with_tool_outputs, monkeypatch,
):
    store, _ = session_with_tool_outputs
    store.update_session("session-1", status="running")
    from openprogram.context.nodes import Call, ROLE_CODE
    from openprogram.execution import CapabilitySet, ExecutionStore, ExecutionStatus
    from openprogram.store import SessionNodeWriter

    node_id = "system-access-node"
    SessionNodeWriter(store, "session-1").append(Call(
        id=node_id,
        role=ROLE_CODE,
        name="agentic_workflow",
        output="",
        metadata={
            "status": "interrupted",
            "error": "Worker restarted before this turn finished",
            "interrupted_at": 1.0,
            "execution_kind": "agentic_function",
        },
    ))
    executions = ExecutionStore(store.root_path.parent / "executions.sqlite")
    revision = executions.create_revision(manifest={"entrypoint": "agent"})
    execution = executions.admit_execution(
        execution_id="execution-system-access-load",
        run_id="run-system-access-load",
        session_id="session-1",
        revision_id=revision.revision_id,
        input_ref="input:system-access-load",
        input_hash="hash:system-access-load",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="config:system-access-load",
        assistant_message_id=node_id,
        capabilities=CapabilitySet(pause=True),
    )
    executions.transition_execution(
        execution.execution_id,
        expected_version=execution.status_version,
        target=ExecutionStatus.PAUSED,
        reason_code="system_access_required",
    )
    monkeypatch.setattr("openprogram.execution.default_store", lambda: executions)

    ws = FakeWS()
    from openprogram.webui import server
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    asyncio.run(ws_session.handle_load_session(ws, {"session_id": "session-1"}))

    loaded = next(frame for frame in ws.frames if frame["type"] == "session_loaded")
    node = next(message for message in loaded["data"]["messages"] if message["id"] == node_id)
    assert node["status"] == "paused"
    assert "Worker restarted" not in str(node)
    assert loaded["data"]["status"] == "idle"
    assert loaded["data"]["run_active"] is False


def test_get_full_tool_output_returns_original_content(
    session_with_tool_outputs,
):
    _, large = session_with_tool_outputs
    ws = FakeWS()

    asyncio.run(ws_session.handle_get_full_tool_output(ws, {
        "session_id": "session-1",
        "message_id": "assistant-1",
        "node_id": "tool-large",
        "request_id": "request-1",
    }))

    assert ws.frames == [{
        "type": "full_tool_output",
        "data": {
            "session_id": "session-1",
            "message_id": "assistant-1",
            "node_id": "tool-large",
            "request_id": "request-1",
            "result": large,
        },
    }]
