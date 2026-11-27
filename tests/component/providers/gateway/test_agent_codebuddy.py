"""CodeBuddy Code, after yetone/magpie ``internal/agent/codebuddy_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def codebuddy(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import codebuddy as module
    monkeypatch.delenv("CODEBUDDY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    folder = agent_home / ".codebuddy"
    folder.mkdir()
    return folder


def test_codebuddy_models_and_session_model(codebuddy, models) -> None:
    (codebuddy / "models.json").write_text(
        '{"models":[{"id":"my-model","url":"https://x/v1/chat/completions","apiKey":"sk"}],"note":"mine"}')
    (codebuddy / "settings.json").write_text('{"model": "my-model", "theme": "dark"}')
    engine.connect("codebuddy", {"model": "deepseek/deepseek-chat"})
    doc = json.loads((codebuddy / "models.json").read_text())
    assert [m["id"] for m in doc["models"]] == ["my-model", "deepseek/deepseek-chat", "openai/gpt-5"]
    assert doc["models"][1]["apiKey"] == token_for("codebuddy") and doc["models"][1]["url"] == V1 + "/chat/completions"
    assert doc["note"] == "mine"
    assert json.loads((codebuddy / "settings.json").read_text()) == {"model": "deepseek/deepseek-chat", "theme": "dark"}

    # a sync updates the list, not the session's model
    models([{"id": "later/m1", "name": "m1"}])
    engine.sync("codebuddy")
    assert [m["id"] for m in json.loads((codebuddy / "models.json").read_text())["models"]] == ["my-model", "later/m1"]
    assert json.loads((codebuddy / "settings.json").read_text())["model"] == "deepseek/deepseek-chat"

    engine.disconnect("codebuddy")
    assert json.loads((codebuddy / "models.json").read_text()) == {
        "models": [{"id": "my-model", "url": "https://x/v1/chat/completions", "apiKey": "sk"}], "note": "mine"}
    assert json.loads((codebuddy / "settings.json").read_text()) == {"model": "my-model", "theme": "dark"}


def test_codebuddy_new_file_is_its_docs_object(codebuddy, monkeypatch, agent_home) -> None:
    monkeypatch.setenv("CODEBUDDY_CONFIG_DIR", str(agent_home / "cb"))
    engine.connect("codebuddy", {"model": "openai/gpt-5"})
    doc = json.loads((agent_home / "cb" / "models.json").read_text())
    assert isinstance(doc, dict) and [m["id"] for m in doc["models"]] == ["deepseek/deepseek-chat", "openai/gpt-5"]
    engine.disconnect("codebuddy")
    assert not (agent_home / "cb" / "models.json").exists() and not (agent_home / "cb" / "settings.json").exists()
