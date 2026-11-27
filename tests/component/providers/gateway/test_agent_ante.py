"""Ante, after yetone/magpie ``internal/agent/ante_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine

from .conftest import MODELS

from .conftest import keyed_base

def V1() -> str:
    return keyed_base('ante')


@pytest.fixture
def ante(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import ante as module
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    return agent_home / ".ante"


def test_ante_connect_sync_and_restore(ante, models, monkeypatch) -> None:
    ante.mkdir()
    catalog = ante / "catalog.json"
    settings = ante / "settings.json"
    user_catalog = '{\n  "providers": {\n    "mine": {\n      "base_url": "http://x"\n    }\n  },\n  "models": {}\n}\n'
    user_settings = '{\n  "provider": "anthropic",\n  "model": "claude-opus",\n  "theme": "dark"\n}\n'
    catalog.write_text(user_catalog)
    settings.write_text(user_settings)

    engine.connect("ante", {"model": "deepseek/deepseek-chat"})
    entry = json.loads(catalog.read_text())["providers"]["openprogram"]
    assert entry["base_url"] == V1() and entry["wire_style"] == "OpenAiCompatible" and "auth" not in entry
    assert entry["preferred_models"] == [
        {"id": "deepseek/deepseek-chat", "display_name": "DeepSeek Chat", "context_limit": 128000,
         "max_tokens": 8192, "supported_efforts": ["low", "medium", "high"]},
        {"id": "openai/gpt-5", "display_name": "GPT-5"},
    ]
    assert json.loads(settings.read_text()) == {"provider": "openprogram", "model": "deepseek/deepseek-chat",
                                                 "theme": "dark"}

    # a key the user adds inside OpenProgram's entry stays across a sync
    data = json.loads(catalog.read_text())
    data["providers"]["openprogram"]["http_headers"] = {"X": "1"}
    catalog.write_text(json.dumps(data, indent=2))
    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    assert engine.sync("ante")
    entry = json.loads(catalog.read_text())["providers"]["openprogram"]
    assert entry["http_headers"] == {"X": "1"} and entry["preferred_models"][-1] == {"id": "later/m1", "display_name": "m1"}
    assert engine.status("ante")["config"]["drift"] == [] and engine.owned("ante")

    # the gateway moves: the entry is rewired, the user's key in it kept
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["Ante"]
    entry = json.loads(catalog.read_text())["providers"]["openprogram"]
    assert entry["base_url"].startswith("http://127.0.0.1:18200/") and entry["http_headers"] == {"X": "1"}

    # edited inside, the entry still goes whole (magpie DelJSON of its own entry)
    data = json.loads(catalog.read_text())
    data["providers"]["openprogram"]["display_name"] = "Mine now"
    catalog.write_text(json.dumps(data, indent=2))
    engine.disconnect("ante")
    assert json.loads(settings.read_text()) == json.loads(user_settings)
    assert json.loads(catalog.read_text()) == json.loads(user_catalog)


def test_ante_files_it_made_go_again(ante) -> None:
    engine.connect("ante", {"model": "openai/gpt-5"})
    assert (ante / "catalog.json").exists() and (ante / "settings.json").exists()
    engine.disconnect("ante")
    assert not (ante / "catalog.json").exists() and not (ante / "settings.json").exists()


def test_ante_efforts_and_max_tokens() -> None:
    from openprogram.models.gateway.agents.ante import efforts, max_tokens
    assert efforts(["none", "minimal", "high", "xhigh"]) == ["min", "high", "xhigh"]
    assert efforts(["none"]) == []
    # Ante skips an entry whose max_tokens is not under its window (#1179)
    assert max_tokens({"context_window": 128000, "max_tokens": 128000}) == 127999
    assert max_tokens({"context_window": 128000, "max_tokens": 300000}) == 127999
    assert max_tokens({"context_window": 0, "max_tokens": 4096}) == 4096


def test_ante_home(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import ante
    monkeypatch.setenv("ANTE_HOME", str(agent_home / "elsewhere"))
    assert ante.catalog_path() == agent_home / "elsewhere" / "catalog.json"
