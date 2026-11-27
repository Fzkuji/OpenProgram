"""Droid, after yetone/magpie ``internal/agent/droid_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

GW = "http://127.0.0.1:18100/gateway"

USER = """{
  "customModels": [
    {
      "model": "kimi-k2",
      "displayName": "Kimi K2 [Groq]",
      "baseUrl": "https://api.groq.com/openai/v1",
      "apiKey": "gsk-mine",
      "provider": "generic-chat-completion-api"
    }
  ],
  "sessionDefaultSettings": {
    "model": "custom:Kimi-K2-[Groq]-0"
  },
  "theme": "dark"
}
"""


@pytest.fixture
def droid(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import droid as module
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    path = agent_home / ".factory" / "settings.json"
    path.parent.mkdir()
    path.write_text(USER)
    return path


def _entries(path):
    return {e.get("id", e["model"]): e for e in json.loads(path.read_text())["customModels"]}


def test_droid_connect_sync_and_restore(droid, models) -> None:
    engine.connect("droid", {"model": "deepseek/deepseek-chat"})
    data = json.loads(droid.read_text())
    assert data["customModels"][0]["model"] == "kimi-k2"  # the user's stays first
    assert data["sessionDefaultSettings"]["model"] == "custom:openprogram/deepseek/deepseek-chat"
    listed = _entries(droid)
    assert listed["custom:openprogram/deepseek/deepseek-chat"] == {
        "model": "deepseek/deepseek-chat", "id": "custom:openprogram/deepseek/deepseek-chat",
        "displayName": "DeepSeek Chat", "baseUrl": f"{GW}/openai/v1", "apiKey": token_for("droid"),
        "provider": "generic-chat-completion-api", "maxContextLimit": 128000, "maxOutputTokens": 8192,
        "reasoningEffort": "medium", "noImageSupport": True}
    gpt = listed["custom:openprogram/openai/gpt-5"]
    assert gpt["provider"] == "openai" and gpt["baseUrl"] == f"{GW}/openai/v1" and gpt["noImageSupport"] is False
    assert "reasoningEffort" not in gpt
    assert engine.status("droid")["config"]["drift"] == []

    models([*MODELS, {"id": "anth/claude-opus-4-7", "name": "Opus", "reasoning": True, "api": "anthropic-messages"}])
    assert engine.sync("droid")
    opus = _entries(droid)["custom:openprogram/anth/claude-opus-4-7"]
    assert opus["provider"] == "anthropic" and opus["baseUrl"] == f"{GW}/anthropic"
    assert opus["reasoningEffort"] == "medium" and opus["baseModelId"] == "claude-opus-4-7"
    assert json.loads(droid.read_text())["sessionDefaultSettings"]["model"].endswith("deepseek/deepseek-chat")

    engine.disconnect("droid")
    assert droid.read_text() == USER


def test_droid_keeps_an_entry_edited_by_hand(droid) -> None:
    engine.connect("droid", {"model": "openai/gpt-5"})
    data = json.loads(droid.read_text())
    for e in data["customModels"]:
        if e.get("id") == "custom:openprogram/openai/gpt-5":
            e["provider"] = "generic-chat-completion-api"  # back on Chat, by their own choice
            e["maxContextLimit"] = 300000
            e["extraArgs"] = {"reasoning_effort": "high"}
            e["index"] = 7
        if e.get("id") == "custom:openprogram/deepseek/deepseek-chat":
            e["displayName"] = "DS (mine)"
            del e["noImageSupport"]
    droid.write_text(json.dumps(data, indent=2))
    for step in (lambda: engine.sync("droid"), lambda: engine.connect("droid", {"model": "deepseek/deepseek-chat"})):
        step()
        listed = _entries(droid)
        gpt = listed["custom:openprogram/openai/gpt-5"]
        assert gpt["provider"] == "generic-chat-completion-api" and gpt["baseUrl"] == f"{GW}/openai/v1"
        assert gpt["maxContextLimit"] == 300000 and gpt["extraArgs"] and "index" not in gpt
        assert gpt["apiKey"] == token_for("droid")
        ds = listed["custom:openprogram/deepseek/deepseek-chat"]
        assert ds["displayName"] == "DS (mine)" and "noImageSupport" not in ds
    engine.disconnect("droid")
    assert [e["model"] for e in json.loads(droid.read_text())["customModels"]] == ["kimi-k2"]


def test_droid_fresh_and_override(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import droid as module
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    monkeypatch.setenv("FACTORY_HOME_OVERRIDE", str(agent_home / "elsewhere"))
    path = agent_home / "elsewhere" / ".factory" / "settings.json"
    engine.connect("droid", {"model": "openai/gpt-5"})
    assert json.loads(path.read_text())["sessionDefaultSettings"] == {"model": "custom:openprogram/openai/gpt-5"}
    engine.disconnect("droid")
    assert not path.exists()


def test_droid_wants_its_own_rule() -> None:
    from openprogram.models.gateway.agents.droid import wants
    cases = {
        "ultrafast/gpt-6.1-sol": "openai", "x/GPT5": "openai", "x/gpt_4o": "openai", "codex/gpt-5.5": "openai",
        "x/gpt-5.3-codex": "openai", "x/my-codex-proxy-model": "openai", "x/o3": "openai", "x/o4-mini": "openai",
        "x/o1.preview": "openai", "x/gpt-oss-120b": "", "x/proto1": "", "x/o10x": "",
        "x/claude-opus-4-7": "anthropic", "or/anthropic/sonnet-next": "anthropic", "x/HAIKU": "anthropic",
        "deepseek/pro": "", "x/kimi-k2.5": "",
    }
    for model, want in cases.items():
        assert wants(model) == want, model


def test_droid_reasoning_effort() -> None:
    from openprogram.models.gateway.agents.droid import effort
    assert effort({"reasoning": True, "thinking_levels": ["low", "medium", "high"]}) == "medium"
    assert effort({"reasoning": True, "thinking_levels": ["low", "high"]}) == "high"
    assert effort({"reasoning": True, "thinking_levels": ["xhigh"]}) == "xhigh"
    assert effort({"reasoning": True, "thinking_levels": ["low", "high"], "default_thinking_level": "low"}) == "low"
    assert effort({"reasoning": True}) == "medium"
    assert effort({"reasoning": False}) == ""


def test_droid_picks_the_api_the_model_is_served_on() -> None:
    """magpie droidEntriesAt: the name droid reads first, then the model's API."""
    from openprogram.models.gateway.agents.droid import _provider
    assert _provider({"id": "acme/kimi-k2", "api": "openai-responses"}) == "openai"
    assert _provider({"id": "acme/kimi-k2", "api": "azure-openai-responses"}) == "openai"
    assert _provider({"id": "zai/glm-5", "api": "anthropic-messages"}) == "anthropic"
    assert _provider({"id": "deepseek/deepseek-chat", "api": "openai-completions"}) == "generic-chat-completion-api"
    assert _provider({"id": "ultrafast/gpt-6.1-sol", "api": "openai-completions"}) == "openai"
    assert _provider({"id": "router/claude-sonnet-5", "api": "openai-responses"}) == "anthropic"
    assert _provider({"id": "openai/gpt-5"}) == "openai"  # by its name
    assert _provider({"id": "anthropic/haiku-x"}) == "anthropic"
    assert _provider({"id": "google/gemini-3", "api": "google-generative-ai"}) == "generic-chat-completion-api"
