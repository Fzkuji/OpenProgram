"""A branch's harness is a run setting like its model, effort and Agent.

Switching the branch to Codex takes Codex's default model and agent and
keeps the branch's OpenProgram model and effort; switching back restores
them. A pick on a branch that is not selected writes only that branch, and
a model pick keeps the harness consistent with the model it names.
"""
import asyncio
import json

import pytest
from fastapi import FastAPI

from tests.unit.dag.test_rewind_modes import store  # noqa: F401 - fixture
from tests.unit.webui.branches.test_model_pick_branch import picked  # noqa: F401 - fixture


@pytest.fixture
def harness_routes(picked, monkeypatch):  # noqa: F811
    from openprogram.interfaces.server.routes.execution import harnesses as routes
    monkeypatch.setattr(routes, "emit_ws_frame", picked.frames.append)
    app = FastAPI()
    routes.register(app)
    endpoints = {r.path: r.endpoint for r in app.routes if "POST" in getattr(r, "methods", ())}

    def post(path, body):
        result = asyncio.run(endpoints[path]("s", body))
        return result.status_code, json.loads(result.body)

    return post


def test_codex_takes_its_default_model_and_openprogram_comes_back(store, picked, harness_routes):  # noqa: F811
    from openprogram.interfaces.server import server
    status, data = harness_routes("/api/sessions/{session_id}/harness", {"harness": "codex", "model": "gpt-6-astra"})
    assert status == 200, data
    row = store.get_session("s")
    assert (row["harness"], row["provider_override"], row["model_override"]) == ("codex", "codex-harness", "gpt-6-astra")
    assert row["thinking_effort"] is None and row["harness_agent"] is None
    assert row["harness_return"] == {"provider_override": "p", "model_override": "selected-model", "thinking_effort": "low"}
    assert "runtime" not in server._sessions["s"], "the next turn builds what the branch now runs"
    assert data["chat"]["harness"] == "codex"
    assert {"type": "agent_settings_changed", "data": {}} in picked.frames

    status, _ = harness_routes("/api/sessions/{session_id}/harness_agent", {"agent": "personality:friendly"})
    assert status == 200
    assert store.get_session("s")["harness_agent"] == "personality:friendly"

    status, _ = harness_routes("/api/sessions/{session_id}/harness", {"harness": "openprogram"})
    assert status == 200
    row = store.get_session("s")
    assert (row["provider_override"], row["model_override"], row["thinking_effort"]) == ("p", "selected-model", "low")
    assert (row["harness"], row["harness_agent"], row["harness_return"]) == (None, None, None)


def test_a_switch_on_a_branch_that_is_not_selected_writes_only_that_branch(store, picked, harness_routes):  # noqa: F811
    from openprogram.interfaces.server import server
    status, data = harness_routes("/api/sessions/{session_id}/harness",
                                  {"harness": "codex", "model": "gpt-6-astra", "branch_id": picked.a})
    assert status == 200, data
    row = store.get_session("s")
    config = row["branch_refs"][picked.a]["config"]
    assert (config["harness"], config["model_override"]) == ("codex", "gpt-6-astra")
    assert row.get("harness") is None and row["model_override"] == "selected-model"
    assert server._sessions["s"]["runtime"] is picked.live, "the selected branch keeps its runtime"


def test_a_harness_not_on_this_computer_is_refused_with_why(store, picked, harness_routes, monkeypatch):  # noqa: F811
    from openprogram.runtime.harnesses import models
    monkeypatch.setattr(models, "read_harness", lambda _id, **_: {"installed": False, "models": []})
    status, data = harness_routes("/api/sessions/{session_id}/harness", {"harness": "codex"})
    assert (status, data["code"]) == (409, "not_installed")
    assert store.get_session("s").get("harness") is None


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_a_model_pick_keeps_the_harness_with_its_model(store, picked, harness_routes, transport):  # noqa: F811
    harness_routes("/api/sessions/{session_id}/harness", {"harness": "codex", "model": "gpt-6-astra"})
    status, _ = picked.pick(transport, {"provider": "codex-harness", "model": "openprogram:q/m"})
    assert status == 200
    assert store.get_session("s")["harness"] == "codex"
    status, _ = picked.pick(transport, {"provider": "q", "model": "picked"})
    assert status == 200
    row = store.get_session("s")
    assert (row["harness"], row["harness_return"], row["provider_override"]) == (None, None, "q")
