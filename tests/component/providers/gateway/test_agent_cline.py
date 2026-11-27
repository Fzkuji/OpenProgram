"""Cline, after yetone/magpie ``internal/agent/cline_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def cline(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import cline as module
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    data = agent_home / ".cline" / "data"
    (data / "settings").mkdir(parents=True)
    return data


def test_cline_connect_sync_and_restore(cline, models, monkeypatch) -> None:
    providers = cline / "settings" / "providers.json"
    user = {"version": 1, "lastUsedProvider": "anthropic", "modes": {}, "providers": {
        "anthropic": {"settings": {"provider": "anthropic", "apiKey": "sk", "model": "claude",
                                   "reasoning": {"effort": "high"}}, "updatedAt": "t", "tokenSource": "manual"},
        "openai-compatible": {"settings": {"provider": "openai-compatible", "model": "gpt-4o"}}}}
    providers.write_text(json.dumps(user, indent=2) + "\n")

    engine.connect("cline", {"model": "deepseek/deepseek-chat"})
    data = json.loads(providers.read_text())
    assert data["lastUsedProvider"] == "openai-compatible"
    settings = data["providers"]["openai-compatible"]["settings"]
    assert settings == {"provider": "openai-compatible", "apiKey": token_for("cline"), "model": "deepseek/deepseek-chat",
                        "baseUrl": V1, "headers": {"User-Agent": "cline"},
                        # the effort of the provider in use goes with the pick
                        "reasoning": {"enabled": True, "effort": "high"}}
    listed = json.loads((cline / "settings" / "models.json").read_text())
    assert listed["version"] == 1
    entry = listed["providers"]["openai-compatible"]
    assert entry["provider"] == {"name": "OpenProgram", "baseUrl": V1, "defaultModelId": "deepseek/deepseek-chat"}
    assert entry["models"]["deepseek/deepseek-chat"] == {
        "id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "capabilities": ["streaming", "tools", "reasoning"],
        "contextWindow": 128000, "maxTokens": 8192}
    assert entry["models"]["openai/gpt-5"]["capabilities"] == ["streaming", "tools", "images"]

    # an override the user set on a listed model stays across a sync
    entry["models"]["openai/gpt-5"]["temperature"] = 0.2
    (cline / "settings" / "models.json").write_text(json.dumps(listed))
    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    assert engine.sync("cline")
    synced = json.loads((cline / "settings" / "models.json").read_text())["providers"]["openai-compatible"]["models"]
    assert synced["openai/gpt-5"]["temperature"] == 0.2 and "later/m1" in synced

    # a key the user adds inside the provider entry, and Cline's new stamp: kept, no drift
    data = json.loads(providers.read_text())
    data["providers"]["openai-compatible"].update({"note": "mine", "updatedAt": "later"})
    providers.write_text(json.dumps(data, indent=2))
    assert engine.status("cline")["config"]["drift"] == [] and engine.owned("cline")
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["Cline"]
    entry = json.loads(providers.read_text())["providers"]["openai-compatible"]
    assert entry["settings"]["baseUrl"].startswith("http://127.0.0.1:18200/") and entry["note"] == "mine"

    # edited inside, the entries still go whole and the user's own comes back
    entry["settings"]["model"] = "theirs"
    data = json.loads(providers.read_text())
    data["providers"]["openai-compatible"] = entry
    providers.write_text(json.dumps(data, indent=2))
    engine.disconnect("cline")
    assert json.loads(providers.read_text()) == user
    assert not (cline / "settings" / "models.json").exists()


def test_cline_extension_state(cline) -> None:
    state, secrets = cline / "globalState.json", cline / "secrets.json"
    state.write_text(json.dumps({"actModeApiProvider": "anthropic", "actModeOpenAiModelInfo": {"x": 1}, "other": 2}))
    engine.connect("cline", {"model": "openai/gpt-5"})
    got = json.loads(state.read_text())
    assert got["actModeApiProvider"] == got["planModeApiProvider"] == "openai"
    assert got["actModeOpenAiModelId"] == "openai/gpt-5" and got["openAiBaseUrl"] == V1
    assert "actModeOpenAiModelInfo" not in got
    assert json.loads(secrets.read_text()) == {"openAiApiKey": token_for("cline")}
    engine.disconnect("cline")
    assert json.loads(state.read_text()) == {"actModeApiProvider": "anthropic", "actModeOpenAiModelInfo": {"x": 1},
                                             "other": 2}
    assert not secrets.exists()
    assert not (cline / "settings" / "providers.json").exists()


def test_cline_reasoning() -> None:
    from openprogram.models.gateway.agents.cline import effort_of, reasoning
    assert reasoning("none") == {"enabled": False}
    assert effort_of({"enabled": False, "effort": "high"}) == "none"
    assert effort_of({"effort": "low"}) == "low" and effort_of(None) == ""
