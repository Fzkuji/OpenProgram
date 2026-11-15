"""Hydration never combines one branch's transcript with another's controls."""
import asyncio
import json
import pytest
from tests.unit.dag.test_rewind_modes import Session, store, _append
from tests.unit.dag.test_shared_workspace_rewind import _checkout_server


@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("keep_switching", [False, True])
def test_checkout_during_load_retries_the_entire_branch_snapshot(store, monkeypatch, flat, keep_switching):
    from openprogram.interfaces.server import server, runtime_management
    from openprogram.interfaces.server.ws_actions.session import load
    from openprogram.interfaces.server.routes.chat_branches import post_chat_checkout
    from openprogram.interfaces.server.transcript import publisher
    from openprogram.interfaces.server.transcript.turn_index import forget_views
    _checkout_server(monkeypatch, store)
    publisher.stop()
    forget_views()
    session = Session(store, "s")
    session.turn_edits()
    store.update_session("s", agent_id="alpha", thinking_effort="high", provider_override="p", model_override="a", web_search=True)
    _append(store, "s", "ub", "user", "ROOT")
    _append(store, "s", "ab", "assistant", "ub")
    assert asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": "ab"})).status_code == 200
    store.update_session("s", agent_id="beta", thinking_effort="low", provider_override="p", model_override="b",
                         web_search=False, tools_override=["read"], permission_mode="ask", service_tier="priority")
    branch_b = store.get_session("s")["active_branch_id"]
    assert asyncio.run(post_chat_checkout({"session_id": "s", "msg_id": "a1"})).status_code == 200
    conv = {"id": "s", "head_id": "a1", "messages": store.get_branch("s")}
    server._sessions["s"] = conv
    monkeypatch.setattr(runtime_management, "_enabled_model_keys", lambda: {("p", "a"), ("p", "b")})
    switched = False

    async def interleave(*_args):
        nonlocal switched
        if not switched or keep_switching:
            target = "a1" if switched else "ab"
            switched = True
            response = await post_chat_checkout({"session_id": "s", "msg_id": target})
            assert response.status_code == 200

    async def after(*_args):
        pass

    monkeypatch.setattr(load, "_mirror_branch", interleave)
    monkeypatch.setattr(load, "_after_session_loaded", after)

    class WS:
        _history_protocol = 0 if flat else 3
        def __init__(self): self.frames = []
        async def send_text(self, payload): self.frames.append(json.loads(payload))

    ws = WS()
    try:
        if keep_switching:
            from openprogram.interfaces.server.ws_errors import OperationError
            with pytest.raises(OperationError, match="session_read_failed") as error:
                asyncio.run(load.handle_load_session(ws, {"session_id": "s"}))
            assert error.value.retryable
            assert not ws.frames
            return
        asyncio.run(load.handle_load_session(ws, {"session_id": "s"}))
        [data] = [frame["data"] for frame in ws.frames if frame["type"] == "session_loaded"]
        assert data["head_id"] == "ab"
        if not flat:
            assert data["window"]["head_id"] == "ab"
        assert data["active_branch_id"] == branch_b
        assert data["agent_id"] == "beta"
        assert data["provider_info"]["model"] == "b"
        assert data["settings"]["thinking_effort"] == "low"
        assert data["settings"]["web_search"] is False
        assert data["settings"]["tools_override"] == ["read"]
        assert data["settings"]["permission_mode"] == "ask"
        assert data["settings"]["service_tier"] == "priority"
    finally:
        publisher.release(ws)


def test_snapshot_provider_keeps_agent_fallback_without_reading_another_branch(monkeypatch):
    from types import SimpleNamespace
    from openprogram.interfaces.server import runtime_management as runtime
    monkeypatch.setattr(runtime, "_enabled_model_keys", lambda: {("p", "agent-model")})
    monkeypatch.setattr("openprogram.runtime.conversation.session_model.read_session_chat_model", lambda _sid: ("wrong", "other-branch"))
    monkeypatch.setattr("openprogram.runtime.agent_registry.manager.get", lambda _id: SimpleNamespace(model=SimpleNamespace(provider="p", id="agent-model")))
    result = runtime._provider_info_from_snapshot({"id": "s", "agent_id": "alpha"})
    assert result["provider"] == "p"
    assert result["model"] == "agent-model"
