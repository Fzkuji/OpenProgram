"""Command Code, after yetone/magpie ``internal/agent/commandcode_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine

from .conftest import keyed_base

def V1() -> str:
    return keyed_base('commandcode')


@pytest.fixture
def folder(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import commandcode
    monkeypatch.setattr(agents, "all_agents", lambda: [commandcode.agent()])
    made = agent_home / ".commandcode"
    made.mkdir()
    return made


def test_commandcode_provider_and_model(folder) -> None:
    providers, settings = folder / "providers.json", folder / "settings.json"
    providers.write_text('{"provider":{"mine":{"name":"Mine","api":"openai-completions",'
                         '"baseURL":"https://x","apiKey":"$X","models":{"a":{}}}}}')
    settings.write_text('{"model": "mine/a", "reasoningEffort": {"mine/a": "high"}}')
    engine.connect("commandcode", {"model": "deepseek/deepseek-chat"})
    data = json.loads(providers.read_text())["provider"]
    assert data["mine"]["apiKey"] == "$X"
    ours = data["openprogram"]
    assert ours["apiKey"] is False and ours["api"] == "openai-completions" and ours["baseURL"] == V1()
    assert ours["models"]["deepseek/deepseek-chat"] == {
        "name": "DeepSeek Chat", "reasoning": True, "reasoningEfforts": ["low", "medium", "high"],
        "contextWindow": 128000, "maxOutput": 8192}
    assert ours["models"]["openai/gpt-5"] == {"name": "GPT-5"}
    s = json.loads(settings.read_text())
    assert s["model"] == "openprogram/deepseek/deepseek-chat" and s["modelProvider"] == "openprogram"

    # a key the user set in OpenProgram's entry stays when it is written again
    whole = json.loads(providers.read_text())
    whole["provider"]["openprogram"]["headers"] = {"X": "1"}
    providers.write_text(json.dumps(whole))
    engine.connect("commandcode", {"model": "openai/gpt-5"})
    assert json.loads(providers.read_text())["provider"]["openprogram"]["headers"] == {"X": "1"}

    engine.disconnect("commandcode")
    assert json.loads(settings.read_text()) == {"model": "mine/a", "reasoningEffort": {"mine/a": "high"}}


def test_commandcode_restores_its_files(folder) -> None:
    engine.connect("commandcode", {"model": "openai/gpt-5"})
    engine.disconnect("commandcode")
    assert not (folder / "providers.json").exists() and not (folder / "settings.json").exists()


def test_commandcode_notice_names_its_sign_in(folder) -> None:
    from openprogram.models.gateway.agents import commandcode
    assert "cmd login" in commandcode.agent().notice()
