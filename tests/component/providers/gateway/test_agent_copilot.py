"""Copilot CLI's BYOK providers, after yetone/magpie ``internal/agent/copilot_byok_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def copilot(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import copilot
    monkeypatch.setattr(agents, "all_agents", lambda: [copilot.agent()])
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.delenv("COPILOT_PROVIDERS_CONFIG", raising=False)
    folder = agent_home / ".copilot"
    folder.mkdir()
    return folder


def test_copilot_owns_its_entries_only(copilot, models) -> None:
    from openprogram.models.gateway.keys import token_for

    from .conftest import MODELS
    providers = copilot / "providers.json"
    user = {"providers": [{"name": "mine", "type": "openai", "baseUrl": "https://x/v1", "apiKey": "k"}],
            "models": [{"id": "m", "provider": "mine"}], "extra": 1}
    providers.write_text(json.dumps(user, indent=2) + "\n")
    (copilot / "settings.json").write_text('{"model": "auto", "effortLevel": "high"}\n')

    engine.connect("copilot", {"model": "deepseek/deepseek-chat"})
    data = json.loads(providers.read_text())
    assert data["providers"][0]["name"] == "mine" and data["extra"] == 1
    assert data["providers"][1] == {"name": "openprogram", "type": "openai", "baseUrl": V1,
                                    "apiKey": token_for("copilot")}
    assert data["models"][1] == {"id": "deepseek/deepseek-chat", "provider": "openprogram", "name": "DeepSeek Chat",
                                 "maxOutputTokens": 8192, "maxContextWindowTokens": 128000,
                                 "maxPromptTokens": 128000 - 8192}
    assert data["models"][2] == {"id": "openai/gpt-5", "provider": "openprogram", "name": "GPT-5"}
    settings = json.loads((copilot / "settings.json").read_text())
    assert settings == {"model": "openprogram/deepseek/deepseek-chat", "effortLevel": "high"}

    # what the user added to OpenProgram's provider stays on a sync
    data["providers"][1]["wireApi"] = "completions"
    providers.write_text(json.dumps(data))
    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    engine.sync("copilot")
    data = json.loads(providers.read_text())
    assert data["providers"][1]["wireApi"] == "completions" and data["models"][-1]["id"] == "later/m1"

    # entries the user added after OpenProgram's stay after them: a sync puts
    # OpenProgram's back where they were (magpie copilotWrite)
    data["providers"].append({"name": "late", "type": "openai"})
    data["models"].append({"id": "late-model", "provider": "late"})
    providers.write_text(json.dumps(data))
    engine.sync("copilot")
    data = json.loads(providers.read_text())
    assert [p["name"] for p in data["providers"]] == ["mine", "openprogram", "late"]
    assert [m["provider"] for m in data["models"]][-1] == "late" and data["models"][1]["provider"] == "openprogram"
    data["providers"].pop()
    data["models"].pop()
    providers.write_text(json.dumps(data))

    engine.disconnect("copilot")
    assert json.loads(providers.read_text()) == user
    assert json.loads((copilot / "settings.json").read_text()) == {"model": "auto", "effortLevel": "high"}


def test_copilot_folder_alone_is_not_the_cli(copilot, monkeypatch, tmp_path) -> None:
    from openprogram.models.gateway.agents import base
    from openprogram.models.gateway.agents import copilot as module
    monkeypatch.setattr(base, "find_tool", lambda name: "")
    assert not module.agent().installed()  # the plugins' ~/.copilot says nothing of the CLI
    (copilot / "settings.json").write_text("{}")
    assert module.agent().installed()


def test_an_editors_launcher_is_not_the_cli(tmp_path) -> None:
    from openprogram.models.gateway.agents.copilot import editor_launcher
    install = tmp_path / "Code" / "bin"
    install.mkdir(parents=True)
    (tmp_path / "Code" / "product.json").write_text("{}")
    (install / "copilot").write_text("")
    assert editor_launcher(str(install / "copilot"))
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "copilot").write_text("")
    assert not editor_launcher(str(tmp_path / "cli" / "copilot"))


def test_copilot_providers_file_moves(copilot, monkeypatch, agent_home) -> None:
    monkeypatch.setenv("COPILOT_PROVIDERS_CONFIG", str(agent_home / "byok.json"))
    engine.connect("copilot", {"model": "openai/gpt-5"})
    assert json.loads((agent_home / "byok.json").read_text())["providers"][0]["name"] == "openprogram"
    engine.disconnect("copilot")
    assert not (agent_home / "byok.json").exists() and not (copilot / "settings.json").exists()
