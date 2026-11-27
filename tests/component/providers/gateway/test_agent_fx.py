"""fx, after yetone/magpie ``internal/agent/fx_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine

from .conftest import keyed_base

def V1() -> str:
    return keyed_base('fx')


@pytest.fixture
def settings(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import fx
    monkeypatch.setattr(agents, "all_agents", lambda: [fx.agent()])
    monkeypatch.setattr(fx, "fx_version", lambda: "0.0.17")
    (agent_home / ".fx").mkdir()
    return agent_home / ".fx" / "settings.json"


def test_fx_provider_model_and_restore(settings) -> None:
    settings.write_text('{\n  "provider": "codex",\n  "models": {\n    "codex": "gpt-5"\n  }\n}\n')
    engine.connect("fx", {"model": "openai/gpt-5"})
    data = json.loads(settings.read_text())
    assert data["provider"] == "openprogram" and data["models"] == {"codex": "gpt-5", "openprogram": "openai/gpt-5"}
    ours = data["providers"]["openprogram"]
    assert ours["base_url"] == V1() and ours["auth"] == {"type": "none"} and ours["tool_choice_mode"] == "send"
    meta = ours["model_metadata"]
    assert list(meta) == ["openai/gpt-5", "deepseek/deepseek-chat"]  # the model in use first
    assert meta["openai/gpt-5"] == {"supports_tool_use": True, "supports_vision": True}
    assert meta["deepseek/deepseek-chat"]["reasoning_efforts"] == {
        level: {"reasoning_effort": level} for level in ("low", "medium", "high")}
    assert meta["deepseek/deepseek-chat"]["max_output_tokens"] == 8192

    # what the user set on the provider in fx stays
    data["providers"]["openprogram"]["tool_choice_mode"] = "omit"
    data["providers"]["openprogram"]["reviewer_model"] = "openai/gpt-5"
    settings.write_text(json.dumps(data, indent=2))
    engine.sync("fx")
    ours = json.loads(settings.read_text())["providers"]["openprogram"]
    assert ours["tool_choice_mode"] == "omit" and ours["reviewer_model"] == "openai/gpt-5"
    engine.disconnect("fx")
    back = json.loads(settings.read_text())
    assert back["provider"] == "codex" and back["models"] == {"codex": "gpt-5"}


def test_fx_older_takes_no_efforts(settings, monkeypatch) -> None:
    from openprogram.models.gateway.agents import fx
    monkeypatch.setattr(fx, "fx_version", lambda: "")
    engine.connect("fx", {"model": "deepseek/deepseek-chat"})
    meta = json.loads(settings.read_text())["providers"]["openprogram"]["model_metadata"]
    assert "reasoning_efforts" not in meta["deepseek/deepseek-chat"]
    assert fx.takes_efforts("0.0.17") and fx.takes_efforts("0.1.0") and not fx.takes_efforts("0.0.16")
    engine.disconnect("fx")
    assert not settings.exists()
