"""Old branch requests cannot mutate the currently selected branch."""
import asyncio
import json
import pytest
from fastapi import FastAPI
from tests.unit.dag.test_rewind_modes import Session, store
from tests.unit.dag.test_shared_workspace_rewind import _checkout_server


def endpoint(app, path, method):
    return next(r.endpoint for r in app.routes if r.path == path and method in r.methods)


def test_branch_settings_rejects_stale_and_persists_empty(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    Session(store, "s").turn_edits()
    store.update_session("s", active_branch_id="B", thinking_effort="high", web_search=True)
    from openprogram.interfaces.server.routes.execution.session_agent import register
    app = FastAPI(); register(app)
    save = endpoint(app, "/api/sessions/{session_id}/branch_settings", "POST")
    frames = []
    monkeypatch.setattr(
        "openprogram.interfaces.server.routes.execution.session_agent.emit_ws_frame", frames.append)
    result = asyncio.run(save("s", {"expected_branch_id": "A", "thinking_effort": "low"}))
    assert result.status_code == 409
    assert frames == []
    assert store.get_session("s")["thinking_effort"] == "high"
    result = asyncio.run(save("s", {"expected_branch_id": "B", "reset_fields": ["thinking_effort"],
                                    "web_search": False}))
    assert result.status_code == 200
    # The effort menu's default puts the branch back to inherit.
    assert store.get_session("s")["thinking_effort"] is None
    assert store.get_session("s")["web_search"] is False
    assert json.loads(result.body)["active_branch_id"] == "B"
    # A pane open on this conversation elsewhere rereads its branch.
    assert [frame["type"] for frame in frames] == ["agent_settings_changed"]


def test_model_stale_request_does_not_build_or_change_runtime(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    Session(store, "s").turn_edits()
    store.update_session("s", active_branch_id="B", model_override="old")
    from openprogram.interfaces.server.routes.execution.runtime import register
    app = FastAPI(); register(app)
    result = asyncio.run(endpoint(app, "/api/model", "POST")({
        "session_id": "s", "model": "new", "provider": "p", "expected_branch_id": "A",
    }))
    assert result.status_code == 409
    assert store.get_session("s")["model_override"] == "old"


def test_model_commit_checks_guard_before_live_mirror(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    Session(store, "s").turn_edits()
    store.update_session("s", active_branch_id="B", model_override="old")
    from openprogram.interfaces.server.branch_settings import commit_model
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    import pytest
    conv = {"model_override": "old"}
    with pytest.raises(BranchConfigConflict):
        commit_model("s", {"expected_branch_id": "A"}, "p", "new", conv)
    assert conv == {"model_override": "old"}
    assert store.get_session("s")["model_override"] == "old"


def test_model_first_guarded_write_refreshes_new_identity(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server.branch_settings import commit_model
    from openprogram.interfaces.server import server
    monkeypatch.setattr("openprogram.models.providers.catalog.last_used.record_last_used_model", lambda *_: None)
    conv = {"id": "s"}
    server._sessions["s"] = conv
    row = commit_model("s", {"expected_branch_id": None}, "p", "new", conv)
    assert conv["model_override"] == "new"
    assert conv["active_branch_id"] == row["active_branch_id"]
    assert row["active_branch_id"]


def test_settings_response_and_mirror_use_checkout_winner(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    Session(store, "s").turn_edits()
    store.update_session("s", active_branch_id="A", thinking_effort="high")
    from openprogram.interfaces.server import server
    server._sessions["s"] = {"id": "s", "active_branch_id": "A", "thinking_effort": "high"}
    update = store.update_branch_config
    def interleave(sid, branch_id, values, **guards):
        update(sid, branch_id, values, **guards)
        store.update_session(sid, active_branch_id="B", thinking_effort="max")
    monkeypatch.setattr(store, "update_branch_config", interleave)
    from openprogram.interfaces.server.routes.execution.session_agent import register
    app = FastAPI(); register(app)
    result = asyncio.run(endpoint(app, "/api/sessions/{session_id}/branch_settings", "POST")(
        "s", {"expected_branch_id": "A", "thinking_effort": "low"}))
    data = json.loads(result.body)
    assert data["active_branch_id"] == "B"
    assert data["settings"]["thinking_effort"] == "max"
    assert server._sessions["s"]["thinking_effort"] == "max"


def test_reset_fields_must_name_settings_and_not_repeat_a_value(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server.routes.execution.session_agent import register
    app = FastAPI(); register(app)
    save = endpoint(app, "/api/sessions/{session_id}/branch_settings", "POST")
    for body in ({"reset_fields": ["model_override"]},
                 {"reset_fields": "thinking_effort"},
                 {"reset_fields": ["thinking_effort"], "thinking_effort": "low"},
                 {"thinking_effort": ""}):
        assert asyncio.run(save("s", body)).status_code == 400


def test_invalid_unhashable_settings_is_bad_request(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server.routes.execution.session_agent import register
    app = FastAPI(); register(app)
    result = asyncio.run(endpoint(app, "/api/sessions/{session_id}/branch_settings", "POST")(
        "s", {"thinking_effort": []}))
    assert result.status_code == 400


class SettingsSocket:
    scope = {"state": {}}
    def __init__(self):
        self.frames = []
    async def send_text(self, frame):
        self.frames.append(json.loads(frame))


def test_sandbox_reply_uses_one_post_write_branch_snapshot(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    Session(store, "s").turn_edits()
    store.update_session("s", active_branch_id="A", sandbox_enabled=False)
    from openprogram.interfaces.server.ws_actions.session.run_config import handle_set_sandbox
    from openprogram.interfaces.server import server
    monkeypatch.setattr(server, "_broadcast", lambda *_: None)
    def save(*_, **__):
        store.update_session("s", active_branch_id="B", sandbox_enabled=False)
    monkeypatch.setattr(store, "update_branch_config", save)
    monkeypatch.setattr("openprogram.host.sandbox.ui_state", lambda override: {"sandbox": override})
    ws = SettingsSocket()
    asyncio.run(handle_set_sandbox(ws, {"session_id": "s", "sandbox_enabled": True, "expected_branch_id": "A"}))
    data = ws.frames[-1]["data"]
    assert (data["active_branch_id"], data["sandbox"]) == ("B", False)


def test_permission_reply_uses_one_post_write_branch_snapshot(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    Session(store, "s").turn_edits()
    store.update_session("s", active_branch_id="A")
    from openprogram.interfaces.server.ws_actions.permissions import handle_set_permission
    from openprogram.interfaces.server import server
    monkeypatch.setattr(server, "_broadcast", lambda *_: None)
    monkeypatch.setattr("openprogram.interfaces.server.ws_actions.runtime._trusted_runtime_actor",
                        lambda _: {"interaction": "interactive"})
    def update(*_, **__):
        store.update_session("s", active_branch_id="B", permission_state={"mode": "ask", "version": 4})
        return {"mode": "bypass", "version": 1}
    async def reconcile(*_):
        pass
    monkeypatch.setattr("openprogram.runtime.permissions.update_permission", update)
    monkeypatch.setattr("openprogram.runtime.permissions.reconcile_permission_waits", reconcile)
    ws = SettingsSocket()
    asyncio.run(handle_set_permission(ws, {"session_id": "s", "mode": "bypass", "expected_version": 0, "expected_branch_id": "A"}))
    data = ws.frames[-1]["data"]
    assert (data["active_branch_id"], data["mode"], data["version"]) == ("B", "ask", 4)


def first_send_request():
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _ChatRequest
    return _ChatRequest(cmd={"expected_branch_id": None}, text="hello", session_id="newdraft",
                        agent_id="main", thinking_effort="high", tools_flag=None, web_search_flag=True,
                        permission_mode="ask", sandbox_flag=None, surface_ref=None, service_tier=None,
                        response_format=None, attachments=None)


def test_new_draft_first_send_materializes_exact_accepted_guard(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.chat.turn import _save_run_config
    monkeypatch.setattr("openprogram.host.environments.assign_new_session_environment", lambda *a: "local")
    req = first_send_request()
    cfg, _ = _save_run_config(server, req, {"id": "newdraft"}, "first-user")
    assert cfg.thinking_effort == "high"
    assert cfg.web_search is True
    assert req.cmd["expected_branch_id"] == store.get_session("newdraft")["active_branch_id"]


def test_new_draft_open_pins_no_model_then_first_send(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.chat.turn import _open_session, _save_run_config
    monkeypatch.setattr("openprogram.host.environments.assign_new_session_environment", lambda *a: "local")
    monkeypatch.setattr("openprogram.runtime.conversation.session_model.new_session_chat_model", lambda *_: ("p", "m"))
    monkeypatch.setattr(server, "_user_pinned_provider", None)
    monkeypatch.setattr(server, "_user_pinned_model", None)
    req = first_send_request()
    conv = _open_session(server, req)
    # Opening writes no model (the conversation follows its Agent), so the
    # draft has no branch yet and the first save materializes the guard.
    assert req.cmd["expected_branch_id"] is None
    assert (store.get_session("newdraft") or {}).get("model_override") is None
    cfg, _ = _save_run_config(server, req, conv, "first-user")
    assert cfg.thinking_effort == "high"
    assert req.cmd["expected_branch_id"] == store.get_session("newdraft")["active_branch_id"]
    assert store.get_session("newdraft").get("model_override") is None


def test_existing_branch_null_guard_is_still_rejected_on_send(store, monkeypatch):
    import pytest
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.chat.turn import _save_run_config
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    store.create_session("newdraft", "main")
    req = first_send_request()
    with pytest.raises(BranchConfigConflict):
        _save_run_config(server, req, {"id": "newdraft"}, "first-user")
    assert store.get_session("newdraft").get("thinking_effort") is None


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_guarded_model_endpoint_accepts_only_its_own_draft_initialization(store, monkeypatch, transport):
    from types import SimpleNamespace
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.execution import runtime
    monkeypatch.setattr("openprogram.runtime.conversation.session_model.new_session_chat_model", lambda *_: ("p", "old"))
    monkeypatch.setattr("openprogram.models.providers.catalog.last_used.record_last_used_model", lambda *_: None)
    monkeypatch.setattr(server, "_user_pinned_provider", None)
    monkeypatch.setattr(server, "_user_pinned_model", None)
    monkeypatch.setattr(server, "_create_runtime_for_visualizer", lambda p, m: SimpleNamespace(model=m))
    monkeypatch.setattr(server, "_get_provider_info", lambda *_: {})
    monkeypatch.setattr(server, "_get_thinking_config_for_model", lambda *_: {})
    monkeypatch.setattr(runtime, "emit_ws_frame", lambda *_: None)
    body = {"session_id": "newdraft", "model": "new", "provider": "p", "expected_branch_id": None}
    if transport == "rest":
        app = FastAPI(); runtime.register(app)
        result = asyncio.run(endpoint(app, "/api/model", "POST")(body))
        assert result.status_code == 200, json.loads(result.body)
    else:
        from openprogram.interfaces.server.ws_actions.runtime import handle_switch_model
        monkeypatch.setattr(server, "_broadcast", lambda *_: None)
        socket = SettingsSocket()
        asyncio.run(handle_switch_model(socket, body))
        assert socket.frames[-1]["type"] == "model_switched"
    assert store.get_session("newdraft")["model_override"] == "new"
    assert server._sessions["newdraft"]["model_override"] == "new"


def test_initial_send_guard_does_not_follow_a_different_created_branch(store, monkeypatch):
    _checkout_server(monkeypatch, store)
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.chat.turn import _save_run_config
    from openprogram.interfaces.server.branch_settings import prepare_initial_guard
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    req = first_send_request()
    req.initial_branch_id = prepare_initial_guard(req.session_id, req.cmd)
    store.create_session("newdraft", "main")
    store.update_session("newdraft", active_branch_id="different", thinking_effort="low")
    with pytest.raises(BranchConfigConflict):
        _save_run_config(server, req, {"id": "newdraft"}, "first-user")
    assert store.get_session("newdraft")["thinking_effort"] == "low"
