"""A model pick names its branch, like every other settings write.

A pick on a branch that is not selected writes that branch only: the
selected branch's model, the live runtime and what other panes show of the
selected branch stay as they were. A refused pick says why and which branch
the conversation now shows.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from tests.unit.dag.test_rewind_modes import store  # noqa: F401 - fixture
from tests.unit.dag.test_shared_workspace_rewind import _checkout_server
from tests.unit.agent.test_branch_agent_admission import two_branches


class FrameSocket:
    scope = {"state": {}}

    def __init__(self):
        self.frames = []

    async def send_text(self, frame):
        self.frames.append(json.loads(frame))


@pytest.fixture
def picked(store, monkeypatch):
    """Two branches, ``b`` selected with a live runtime; returns a pick function."""
    _checkout_server(monkeypatch, store)
    a, b = two_branches(store)
    store.update_session("s", provider_override="p", model_override="selected-model")
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.execution import runtime as routes
    live = SimpleNamespace(model="selected-model", close=lambda: None)
    server._sessions["s"] = {"id": "s", "runtime": live, "provider_name": "p",
                             "provider_override": "p", "model_override": "selected-model"}
    built, frames = [], []
    monkeypatch.setattr("openprogram.models.providers.catalog.last_used.record_last_used_model", lambda *_: None)
    monkeypatch.setattr(server, "_create_runtime_for_visualizer",
                        lambda p, m: built.append((p, m)) or SimpleNamespace(model=m, close=lambda: None))
    monkeypatch.setattr(server, "_get_provider_info", lambda *_: {"provider": "p"})
    monkeypatch.setattr(server, "_get_thinking_config_for_model", lambda *_: {})
    monkeypatch.setattr(routes, "emit_ws_frame", frames.append)
    monkeypatch.setattr(server, "_broadcast", lambda frame: frames.append(json.loads(frame)))
    app = FastAPI(); routes.register(app)
    rest = next(r.endpoint for r in app.routes if r.path == "/api/model" and "POST" in r.methods)

    def pick(transport, body):
        body = {"session_id": "s", **body}
        if transport == "rest":
            result = asyncio.run(rest(body))
            return result.status_code, json.loads(result.body)
        from openprogram.interfaces.server.ws_actions.runtime import handle_switch_model
        ws = FrameSocket()
        asyncio.run(handle_switch_model(ws, body))
        frame = ws.frames[-1]
        return (200 if frame["type"] == "model_switched" else 409), frame["data"]

    return SimpleNamespace(a=a, b=b, live=live, frames=frames, built=built, pick=pick)


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_a_pick_on_a_branch_that_is_not_selected_writes_only_that_branch(store, picked, transport):
    from openprogram.interfaces.server import server
    status, data = picked.pick(transport, {"provider": "q", "model": "picked", "branch_id": picked.a})
    assert status == 200, data
    assert (data["provider"], data["model"]) == ("q", "picked")
    assert (data["branch_id"], data["active_branch_id"]) == (picked.a, picked.b)
    # The pane gets that branch's own stored settings and the model's controls.
    assert data["settings"]["thinking_effort"] == "high"
    assert (data["chat"]["provider"], data["chat"]["model"]) == ("q", "picked")
    assert picked.built == [], "no runtime is built for a branch that does not run"
    row = store.get_session("s")
    assert row["branch_refs"][picked.a]["config"]["model_override"] == "picked"
    assert row["branch_refs"][picked.a]["config"]["provider_override"] == "q"
    assert (row["provider_override"], row["model_override"]) == ("p", "selected-model")
    conv = server._sessions["s"]
    assert conv["runtime"] is picked.live and picked.live.model == "selected-model"
    assert conv["model_override"] == "selected-model"
    # Nothing announces the other branch's model as the selected one's.
    assert [f["type"] for f in picked.frames] == []


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_a_pick_on_the_selected_branch_moves_the_runtime_and_is_announced(store, picked, transport):
    from openprogram.interfaces.server import server
    status, data = picked.pick(transport, {"provider": "p", "model": "next", "branch_id": picked.b})
    assert status == 200, data
    assert (data["branch_id"], data["active_branch_id"], data["model"]) == (picked.b, picked.b, "next")
    assert store.get_session("s")["model_override"] == "next"
    assert server._sessions["s"]["runtime"].model == "next"
    assert "provider_changed" in [f["type"] for f in picked.frames]


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_a_refused_pick_says_why_and_names_the_branch_shown(store, picked, transport):
    status, data = picked.pick(transport, {"provider": "p", "model": "x", "branch_id": "branch_missing"})
    assert status == 409
    assert (data["code"], data["active_branch_id"]) == ("branch_gone", picked.b)
    status, data = picked.pick(transport, {"provider": "p", "model": "x", "expected_branch_id": picked.a})
    assert status == 409
    assert (data["code"], data["active_branch_id"]) == ("branch_changed", picked.b)
    assert store.get_session("s")["model_override"] == "selected-model"
    assert picked.frames == []


def test_a_branch_that_is_not_selected_reads_its_own_model_back(store, picked, monkeypatch):
    """After any broadcast drops a pane's model, the pane rereads it: a read
    naming its branch answers with that branch's stored model, not the
    selected one's."""
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server import runtime_management as rm
    from openprogram.interfaces.server.routes.execution import runtime as routes
    monkeypatch.setattr(server, "_init_providers", lambda: None)
    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {("p", "selected-model"), ("q", "picked")})
    monkeypatch.setattr(rm, "_default_is_enabled", lambda *_: True)
    assert picked.pick("rest", {"provider": "q", "model": "picked", "branch_id": picked.a})[0] == 200
    app = FastAPI(); routes.register(app)
    read = next(r.endpoint for r in app.routes if r.path == "/api/agent_settings" and "GET" in r.methods)

    def model(**query):
        data = json.loads(asyncio.run(read(session_id="s", **query)).body)
        return data["branch_id"], data["active_branch_id"], data["chat"]["provider"], data["chat"]["model"]

    assert model(branch_id=picked.a) == (picked.a, picked.b, "q", "picked")
    assert model(branch_id=picked.b) == (picked.b, picked.b, "p", "selected-model")
    assert model() == (picked.b, picked.b, "p", "selected-model")
    # A branch that is gone answers for the selected one, which the pane refuses.
    assert model(branch_id="branch_missing") == (picked.b, picked.b, "p", "selected-model")


def test_a_settings_read_carries_the_stored_settings_of_the_branch_it_answers_for(store, picked, monkeypatch):
    """A pane rereads after ``agent_settings_changed``: the read brings its
    branch's stored effort and web search, so a write made elsewhere shows."""
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server import runtime_management as rm
    from openprogram.interfaces.server.routes.execution import runtime as routes
    monkeypatch.setattr(server, "_init_providers", lambda: None)
    monkeypatch.setattr(rm, "_default_is_enabled", lambda *_: True)
    store.update_branch_config("s", picked.a, {"thinking_effort": "high", "web_search": True})
    store.update_branch_config("s", picked.b, {"thinking_effort": "low", "web_search": False})
    app = FastAPI(); routes.register(app)
    read = next(r.endpoint for r in app.routes if r.path == "/api/agent_settings" and "GET" in r.methods)

    def stored(session_id="s", **query):
        data = json.loads(asyncio.run(read(session_id=session_id, **query)).body)
        settings = data.get("settings")
        return settings and (data["branch_id"], settings["thinking_effort"], settings["web_search"])

    assert stored(branch_id=picked.a) == (picked.a, "high", True)
    assert stored(branch_id=picked.b) == (picked.b, "low", False)
    assert stored() == (picked.b, "low", False)
    assert stored(branch_id="branch_missing") == (picked.b, "low", False)
    # A conversation that does not exist yet has no stored settings to adopt.
    monkeypatch.setattr(server, "_get_or_create_session", lambda *_: None)
    assert stored(session_id="draft") is None
