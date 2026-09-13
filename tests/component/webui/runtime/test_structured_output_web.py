from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.webui.ws_actions.chat import handle_chat


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class FakeWebSocket:
    def __init__(self):
        self.frames = []

    async def send_text(self, value):
        self.frames.append(json.loads(value))


def test_invalid_schema_is_rejected_before_web_dispatch(monkeypatch):
    ws = FakeWebSocket()
    calls = []
    monkeypatch.setattr(
        "openprogram.webui.server._get_or_create_session",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    asyncio.run(handle_chat(ws, {
        "action": "chat",
        "text": "answer",
        "response_format": {"type": "not-a-json-schema-type"},
    }))

    assert calls == []
    assert ws.frames[0]["data"]["type"] == "error"
    assert ws.frames[0]["data"]["code"] == "invalid_schema"
    assert "not-a-json-schema-type" not in json.dumps(ws.frames[0])


def test_web_chat_threads_normalized_response_format_to_existing_dispatch(
    monkeypatch,
):
    from openprogram.agent import run_control
    from openprogram.webui import server as web_server

    ws = FakeWebSocket()
    captured = {}

    class Thread:
        def __init__(self, *, target, args, kwargs, daemon):
            captured.update(target=target, args=args, kwargs=kwargs, daemon=daemon)

        def start(self):
            return None

    # The fake thread never consumes its reservation; keep it local to this test.
    monkeypatch.setattr("openprogram.webui.server._running_tasks", {})
    monkeypatch.setattr("openprogram.webui.ws_actions.chat.threading.Thread", Thread)
    # The inert thread never runs dispatch cleanup; isolate its real registration.
    monkeypatch.setattr(run_control, "_active_exec_runtimes", {})
    monkeypatch.setattr(web_server, "_running_tasks", {})
    monkeypatch.setattr("openprogram.webui.server._is_run_active", lambda _sid: False)
    monkeypatch.setattr("openprogram.webui.server._append_msg", lambda *args: None)
    monkeypatch.setattr("openprogram.webui.server._emit_running_task_event", lambda *args: None)
    monkeypatch.setattr("openprogram.webui.server._get_or_create_session", lambda *args, **kwargs: {"id": "s1"})
    monkeypatch.setattr("openprogram.webui.ws_actions.chat._db_agent_id", lambda _sid: "main")
    monkeypatch.setattr(
        "openprogram.agent.session_config.save_session_run_config",
        lambda *args, **kwargs: type("Cfg", (), {
            "tools_enabled": None,
            "tools_override": None,
            "web_search": False,
            "toolset": None,
            "thinking_effort": None,
            "permission_mode": "ask",
            "sandbox_enabled": None,
        })(),
    )
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db",
        lambda: type("DB", (), {"get_session": lambda self, _sid: {"extra_meta": {}}, "update_session": lambda *args, **kwargs: None})(),
    )

    asyncio.run(handle_chat(ws, {
        "action": "chat",
        "text": "answer",
        "session_id": "s1",
        "response_format": SCHEMA,
    }))

    assert captured["kwargs"]["response_format"].schema == SCHEMA
    assert ws.frames[-1]["type"] == "chat_ack"


def test_function_http_preflights_schema_and_keeps_async_ack(monkeypatch):
    from openprogram.webui.routes import chat as routes_chat

    app = FastAPI()
    routes_chat.register(app)
    seen = {}

    def run(name, kwargs, session_id, anchor_msg_id=None, response_format=None):
        seen.update(name=name, kwargs=kwargs, response_format=response_format)
        return {"session_id": "s1", "msg_id": "m1"}

    monkeypatch.setattr(routes_chat, "run_agentic_function_call", run)
    response = TestClient(app).post(
        "/api/function/demo",
        json={"kwargs": {"value": 1}, "response_format": SCHEMA},
    )

    assert response.status_code == 200
    assert response.json() == {"session_id": "s1", "msg_id": "m1"}
    assert seen["kwargs"] == {"value": 1}
    assert seen["response_format"].schema == SCHEMA


def test_function_http_rejects_invalid_schema_before_dispatch(monkeypatch):
    from openprogram.webui.routes import chat as routes_chat

    app = FastAPI()
    routes_chat.register(app)
    calls = []
    monkeypatch.setattr(
        routes_chat,
        "run_agentic_function_call",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    response = TestClient(app).post(
        "/api/function/demo",
        json={"response_format": {"type": "not-a-json-schema-type"}},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_schema"
    assert calls == []
    assert "not-a-json-schema-type" not in response.text


def test_function_http_forwards_pending_project_before_dispatch(monkeypatch):
    from openprogram.webui.routes import chat as routes_chat

    app = FastAPI()
    routes_chat.register(app)
    seen = {}

    def run(
        name,
        kwargs,
        session_id,
        anchor_msg_id=None,
        response_format=None,
        project_id=None,
    ):
        seen.update(project_id=project_id)
        return {"session_id": "s1", "msg_id": "m1"}

    monkeypatch.setattr(routes_chat, "run_agentic_function_call", run)
    response = TestClient(app).post(
        "/api/function/auto_workflow",
        json={"kwargs": {"task": "research"}, "project_id": "project-1"},
    )

    assert response.status_code == 200
    assert seen["project_id"] == "project-1"


def test_function_http_forwards_exact_origin_page(monkeypatch):
    from openprogram.webui.routes import chat as routes_chat

    app = FastAPI()
    routes_chat.register(app)
    seen = {}

    def run(name, kwargs, session_id, **options):
        seen.update(options)
        return {"session_id": "s1", "msg_id": "m1"}

    monkeypatch.setattr(routes_chat, "run_agentic_function_call", run)
    response = TestClient(app).post(
        "/api/function/gui_agent",
        json={
            "kwargs": {"task": "inspect"},
            "window_id": "window-1",
            "surface_ref": {
                "version": 1,
                "window_id": "window-1",
                "tab_id": "tab-submitted",
            },
        },
    )

    assert response.status_code == 200
    assert seen["origin_window_id"] == "window-1"
    assert seen["surface_ref"] == {
        "version": 1,
        "window_id": "window-1",
        "tab_id": "tab-submitted",
    }


def test_function_http_keeps_legacy_top_level_surface_argument(monkeypatch):
    from openprogram.webui.routes import chat as routes_chat

    app = FastAPI()
    routes_chat.register(app)
    seen = {}

    def run(name, kwargs, session_id, **options):
        seen.update(kwargs=kwargs, options=options)
        return {"session_id": "s1", "msg_id": "m1"}

    monkeypatch.setattr(routes_chat, "run_agentic_function_call", run)
    response = TestClient(app).post(
        "/api/function/gui_agent",
        json={"task": "inspect", "surface": "browser"},
    )

    assert response.status_code == 200
    assert seen["kwargs"] == {"task": "inspect", "surface": "browser"}
    assert "surface_ref" not in seen["options"]


def test_function_http_rejects_surface_from_another_window(monkeypatch):
    from openprogram.webui.routes import chat as routes_chat

    app = FastAPI()
    routes_chat.register(app)
    calls = []
    monkeypatch.setattr(
        routes_chat,
        "run_agentic_function_call",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    response = TestClient(app).post(
        "/api/function/gui_agent",
        json={
            "kwargs": {"task": "inspect"},
            "window_id": "window-1",
            "surface_ref": {"window_id": "window-2", "tab_id": "tab-other"},
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "surface_window_mismatch"
    assert calls == []


def test_function_dispatch_propagates_schema_to_nested_runtime(monkeypatch):
    from openprogram.agent.dispatcher.forced_tool import dispatch_forced_tool_call
    from openprogram.providers.structured_output import normalize_response_format

    tool = type("Tool", (), {"name": "demo", "_is_agentic": True})()
    seen = {}
    monkeypatch.setattr("openprogram.programs.agent_tools", lambda names=None: [tool])
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: tool if name == tool.name else None,
    )
    monkeypatch.setattr(
        "openprogram.agent.process_runner.run_agentic_in_subprocess",
        lambda **kwargs: seen.update(kwargs) or {"ok": True, "runtime_msg_id": None},
    )

    response_format = normalize_response_format(SCHEMA)
    dispatch_forced_tool_call(
        "s1", "", "demo", {}, response_format=response_format,
    )

    assert seen["response_format"] == response_format
