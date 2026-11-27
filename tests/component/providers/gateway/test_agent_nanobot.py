"""nanobot, after yetone/magpie ``internal/agent/nanobot_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def config(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import nanobot
    monkeypatch.setattr(agents, "all_agents", lambda: [nanobot.agent()])
    (agent_home / ".nanobot").mkdir()
    return agent_home / ".nanobot" / "config.json"


def test_nanobot_defaults_and_restore(config) -> None:
    from openprogram.models.gateway.keys import token_for
    user = {"agents": {"defaults": {"model": "anthropic/claude", "provider": "auto", "model_preset": "fast",
                                    "workspace": "~/w"}},
            "providers": {"openrouter": {"apiKey": "k"}}}
    config.write_text(json.dumps(user, indent=2) + "\n")
    engine.connect("nanobot", {"model": "deepseek/deepseek-chat"})
    data = json.loads(config.read_text())
    assert data["agents"]["defaults"] == {"model": "openprogram/deepseek/deepseek-chat", "provider": "openprogram",
                                          "workspace": "~/w", "contextWindowTokens": 128000}
    assert data["providers"]["openprogram"] == {"apiKey": token_for("nanobot"), "apiBase": V1,
                                                "extraHeaders": {"User-Agent": "nanobot"}}
    assert data["providers"]["openrouter"] == {"apiKey": "k"}
    engine.disconnect("nanobot")
    assert json.loads(config.read_text()) == user


def test_nanobot_unknown_window_and_fresh_file(config) -> None:
    engine.connect("nanobot", {"model": "openai/gpt-5"})
    assert "contextWindowTokens" not in json.loads(config.read_text())["agents"]["defaults"]
    engine.disconnect("nanobot")
    assert not config.exists()
