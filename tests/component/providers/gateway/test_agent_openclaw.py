"""OpenClaw, after yetone/magpie ``internal/agent/openclaw_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.edit import ConfigUnreadable
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def openclaw(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import openclaw as module
    for name in ("OPENCLAW_HOME", "OPENCLAW_PROFILE", "OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    path = agent_home / ".openclaw" / "openclaw.json"
    path.parent.mkdir()
    return path


SHAPES = {
    "string": '{\n  "agents": {\n    "defaults": {\n      "model": "anthropic/claude-opus-4-6"\n    }\n  }\n}\n',
    "fallback": '{\n  "agents": {\n    "defaults": {\n      "model": {\n        "primary": "anthropic/claude-opus-4-6",\n'
                '        "fallbacks": ["openai/gpt-5.5"]\n      }\n    }\n  }\n}\n',
    "no model": '{\n  "gateway": {\n    "port": 18789\n  }\n}\n',
    "no file": "",
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_openclaw_shapes(openclaw, name) -> None:
    body = SHAPES[name]
    if body:
        openclaw.write_text(body)
    engine.connect("openclaw", {"model": "deepseek/deepseek-chat"})
    data = json.loads(openclaw.read_text())
    got = data["agents"]["defaults"]["model"]
    if name == "fallback":
        assert got == {"primary": "openprogram/deepseek/deepseek-chat", "fallbacks": ["openai/gpt-5.5"]}
    else:
        assert got == "openprogram/deepseek/deepseek-chat"
    provider = data["models"]["providers"]["openprogram"]
    assert provider["baseUrl"] == V1 and provider["apiKey"] == token_for("openclaw")
    assert provider["api"] == "openai-completions"
    assert provider["models"] == [
        {"id": "deepseek/deepseek-chat", "name": "deepseek/deepseek-chat", "reasoning": True, "input": ["text"],
         "contextWindow": 128000},
        {"id": "openai/gpt-5", "name": "openai/gpt-5", "reasoning": False, "input": ["text", "image"],
         "contextWindow": 128000},
    ]
    engine.disconnect("openclaw")
    if body:
        assert json.loads(openclaw.read_text()) == json.loads(body)
    else:
        assert not openclaw.exists()


def test_openclaw_allow_list(openclaw) -> None:
    body = {"agents": {"defaults": {"model": "deepseek/deepseek-v4-pro", "modelPolicy": {"allow": ["deepseek/*"]}}}}
    openclaw.write_text(json.dumps(body, indent=2))
    engine.connect("openclaw", {"model": "openai/gpt-5"})
    assert json.loads(openclaw.read_text())["agents"]["defaults"]["modelPolicy"]["allow"] == ["deepseek/*", "openprogram/*"]
    assert engine.status("openclaw")["config"]["drift"] == []
    engine.disconnect("openclaw")
    assert json.loads(openclaw.read_text()) == body


def test_openclaw_models_set_and_sync_keeps_theirs(openclaw, models) -> None:
    body = {"agents": {"defaults": {"model": {"primary": "deepseek/v4"}, "models": {"deepseek/v4": {}}}}}
    openclaw.write_text(json.dumps(body, indent=2))
    engine.connect("openclaw", {"model": "openai/gpt-5"})
    data = json.loads(openclaw.read_text())
    assert data["agents"]["defaults"]["models"] == {"deepseek/v4": {}, "openprogram/openai/gpt-5": {}}
    # a key of the user's in OpenProgram's model entry stays on sync
    data["models"]["providers"]["openprogram"]["models"][0]["maxTokens"] = 4096
    openclaw.write_text(json.dumps(data, indent=2))
    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    engine.connect("openclaw", {"model": "openai/gpt-5"})
    listed = json.loads(openclaw.read_text())["models"]["providers"]["openprogram"]["models"]
    assert listed[0]["maxTokens"] == 4096 and listed[-1]["id"] == "later/m1"
    engine.disconnect("openclaw")
    assert json.loads(openclaw.read_text()) == body


def test_openclaw_provider_edits_stay_owned_and_go_whole(openclaw, models, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    body = {"agents": {"defaults": {"model": "deepseek/v4"}}, "models": {"providers": {"mine": {"baseUrl": "x"}}}}
    openclaw.write_text(json.dumps(body, indent=2))
    engine.connect("openclaw", {"model": "openai/gpt-5"})
    data = json.loads(openclaw.read_text())
    provider = data["models"]["providers"]["openprogram"]
    provider["headers"] = {"X-Team": "a"}
    provider["timeoutSeconds"] = 90
    provider["models"][0]["maxTokens"] = 4096
    openclaw.write_text(json.dumps(data, indent=2))
    assert engine.status("openclaw")["config"]["drift"] == [] and engine.owned("openclaw")

    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    assert lifecycle.resync() == ["openclaw"]
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["OpenClaw"]
    provider = json.loads(openclaw.read_text())["models"]["providers"]["openprogram"]
    assert provider["baseUrl"].startswith("http://127.0.0.1:18200/") and provider["headers"] == {"X-Team": "a"}
    assert provider["timeoutSeconds"] == 90 and provider["models"][0]["maxTokens"] == 4096
    assert provider["models"][-1]["id"] == "later/m1"

    engine.disconnect("openclaw")
    assert json.loads(openclaw.read_text()) == body


def test_openclaw_json5_left_alone(openclaw) -> None:
    body = "{ agents: { defaults: { model: 'x/y' } } }\n"
    openclaw.write_text(body)
    with pytest.raises((engine.AgentConfigError, ConfigUnreadable)):
        engine.connect("openclaw", {"model": "openai/gpt-5"})
    assert openclaw.read_text() == body


def test_openclaw_paths(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import openclaw as module
    for name in ("OPENCLAW_HOME", "OPENCLAW_PROFILE", "OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH"):
        monkeypatch.delenv(name, raising=False)
    assert module.config_path() == agent_home / ".openclaw" / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_PROFILE", "work")
    assert module.config_path() == agent_home / ".openclaw-work" / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_PROFILE", "bad profile!")
    assert module.state_dir() == agent_home / ".openclaw"
    monkeypatch.setenv("OPENCLAW_HOME", "~/oc")
    assert module.state_dir() == agent_home / "oc" / ".openclaw"
    monkeypatch.setenv("OPENCLAW_STATE_DIR", "~/state")
    assert module.config_path() == agent_home / "oc" / "state" / "openclaw.json"  # ~ is OPENCLAW_HOME
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", "/x/openclaw.json")
    assert str(module.config_path()) == "/x/openclaw.json"
