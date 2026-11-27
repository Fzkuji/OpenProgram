"""Copilot (JetBrains), after yetone/magpie ``internal/agent/copilotjetbrains_test.go`` (MIT License)."""
from __future__ import annotations

import json
import os
import sys

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

URL = "http://127.0.0.1:18100/gateway/openai/v1/chat/completions"

USER = """{
  "azure-provider-config": {"groupName": "azure", "apiType": "chatCompletions"},
  "azure-api-key": "mine"
}
"""


@pytest.fixture
def copilot(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import copilot_jetbrains as module
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    return module


def test_copilot_jetbrains(copilot, agent_home, models) -> None:
    path = copilot.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(USER)
    engine.connect("copilot-jetbrains", {"model": "deepseek/deepseek-chat"})
    data = json.loads(path.read_text())
    assert data["azure-api-key"] == "mine"
    assert data["openprogram-provider-config"] == {"groupName": "openprogram", "apiType": "chatCompletions"}
    assert data["openprogram-api-key"] == token_for("copilot-jetbrains")
    ds = data["openprogram-models-config"]["deepseek/deepseek-chat"]
    assert ds == {"deploymentUrl": URL, "isRegistered": True, "isCustomModel": True,
                  "modelCapabilities": {"name": "DeepSeek Chat", "maxInputTokens": 128000, "maxOutputTokens": 8192,
                                        "toolCalling": True, "vision": False}}
    gpt = data["openprogram-models-config"]["openai/gpt-5"]["modelCapabilities"]
    assert gpt["maxInputTokens"] == 128000 and gpt["maxOutputTokens"] == 16000 and gpt["vision"] is True

    # a model the user hid in Manage Models stays hidden through a sync
    data["openprogram-models-config"]["openai/gpt-5"]["isRegistered"] = False
    path.write_text(json.dumps(data, indent=2))
    models([*MODELS, {"id": "later/m1", "name": "m1", "context_window": 32000, "max_tokens": 32000}])
    engine.sync("copilot-jetbrains")
    data = json.loads(path.read_text())
    assert data["openprogram-models-config"]["openai/gpt-5"]["isRegistered"] is False
    assert data["openprogram-models-config"]["later/m1"]["modelCapabilities"]["maxOutputTokens"] == 8000

    engine.disconnect("copilot-jetbrains")
    assert json.loads(path.read_text()) == json.loads(USER)


def test_copilot_jetbrains_new_file(copilot, agent_home) -> None:
    path = copilot.config_path()
    engine.connect("copilot-jetbrains", {"model": "openai/gpt-5"})
    assert len(json.loads(path.read_text())) == 3
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    engine.disconnect("copilot-jetbrains")
    assert not path.exists()


def test_copilot_byok_path(copilot, agent_home, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(agent_home / "xdg"))
    assert copilot.config_path() == agent_home / "xdg" / "github-copilot" / "byok.json"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/xdg")
    want = agent_home / ".config" / "github-copilot" / "byok.json"
    if sys.platform == "win32":
        want = agent_home / "AppData" / "Local" / "github-copilot" / "byok.json"
    assert copilot.config_path() == want


def test_copilot_jetbrains_detected(copilot, agent_home, monkeypatch) -> None:
    """Found by the plugin installed in a JetBrains IDE, not by
    ~/.config/github-copilot, which every Copilot signed in here has."""
    monkeypatch.setenv("APPDATA", str(agent_home / "AppData" / "Roaming"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    folder = agent_home / ".config" / "github-copilot"
    folder.mkdir(parents=True)
    (folder / "apps.json").write_text("{}")
    assert not copilot.agent().installed()
    if sys.platform == "darwin":
        ide = agent_home / "Library" / "Application Support" / "JetBrains" / "IntelliJIdea2026.1" / "plugins"
    elif sys.platform == "win32":
        ide = agent_home / "AppData" / "Roaming" / "JetBrains" / "IntelliJIdea2026.1" / "plugins"
    else:
        ide = agent_home / ".local" / "share" / "JetBrains" / "IntelliJIdea2026.1"
    os.makedirs(ide)
    assert not copilot.agent().installed()
    (ide / copilot.PLUGIN).mkdir()
    assert copilot.agent().installed()
