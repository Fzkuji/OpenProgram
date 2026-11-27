"""Empryo, after yetone/magpie ``internal/agent/empryo_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine

from .conftest import keyed_base

def V1() -> str:
    return keyed_base('empryo')


@pytest.fixture
def config(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import empryo
    monkeypatch.setattr(agents, "all_agents", lambda: [empryo.agent()])
    return agent_home / ".empryo" / "config.json"


def test_empryo_provider_and_default(config) -> None:
    config.parent.mkdir()
    user = {"providers": [{"id": "mine", "baseURL": "https://x/v1", "envVar": "X"}], "defaultModel": "mine/a",
            "theme": "dark"}
    config.write_text(json.dumps(user, indent=2) + "\n")
    engine.connect("empryo", {"model": "deepseek/deepseek-chat"})
    data = json.loads(config.read_text())
    assert data["providers"] == [user["providers"][0], {"id": "openprogram", "name": "OpenProgram",
                                                        "baseURL": V1(), "modelsAPI": V1() + "/models"}]
    assert data["defaultModel"] == "openprogram/deepseek/deepseek-chat" and data["theme"] == "dark"
    engine.disconnect("empryo")
    assert json.loads(config.read_text()) == user


def test_empryo_file_made_goes_again(config) -> None:
    engine.connect("empryo", {"model": "openai/gpt-5"})
    assert config.exists()
    engine.disconnect("empryo")
    assert not config.exists()
