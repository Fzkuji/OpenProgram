"""OpenPencil, after yetone/magpie ``internal/agent/openpencil_test.go`` (MIT License)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

V1 = "http://127.0.0.1:18100/gateway/openai/v1"

USER = """{
  "version": 1,
  "theme": "dark",
  "builtin_agents": [
    {
      "id": "builtin-1",
      "preset": "anthropic",
      "display_name": "Anthropic",
      "kind": "anthropic",
      "api_key": "sk-ant-users-own",
      "model": "claude-sonnet-5",
      "models": ["claude-sonnet-5", "claude-opus-5"],
      "base_url": "https://api.anthropic.com",
      "enabled": true
    }
  ],
  "chat_agent": "builtin",
  "chat_model": "builtin:builtin-1:claude-sonnet-5"
}
"""


@pytest.fixture
def pencil(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import openpencil
    monkeypatch.setattr(agents, "all_agents", lambda: [openpencil.agent()])
    monkeypatch.setattr(openpencil, "running", lambda: False)
    monkeypatch.setattr(openpencil, "home", lambda platform=None: agent_home / "config" / "openpencil")
    return agent_home / "config" / "openpencil" / "settings.json"


def test_openpencil_entry_kept_beside_the_users(pencil, models) -> None:
    pencil.parent.mkdir(parents=True)
    pencil.write_text(USER)
    engine.connect("openpencil", {"model": "deepseek/deepseek-chat"})
    data = json.loads(pencil.read_text())
    assert data["builtin_agents"][0]["id"] == "builtin-1" and data["chat_model"].startswith("builtin:builtin-1")
    assert data["builtin_agents"][1] == {
        "id": "openprogram", "preset": "custom", "display_name": "OpenProgram", "kind": "openai-compat",
        "api_key": token_for("openpencil"), "model": "deepseek/deepseek-chat",
        "models": ["deepseek/deepseek-chat", "openai/gpt-5"], "base_url": V1, "enabled": True}

    # the name and on/off given the entry in OpenPencil stay through a sync
    data["builtin_agents"][1].update(display_name="Gateway", enabled=False)
    pencil.write_text(json.dumps(data, indent=2))
    models([{"id": "only/one", "name": "One"}])
    engine.sync("openpencil")
    entry = json.loads(pencil.read_text())["builtin_agents"][1]
    assert (entry["display_name"], entry["enabled"], entry["model"]) == ("Gateway", False, "only/one")
    assert "models" not in entry  # written only for two or more

    engine.disconnect("openpencil")
    assert json.loads(pencil.read_text()) == json.loads(USER)


def test_openpencil_makes_its_file_with_version_1(pencil) -> None:
    engine.connect("openpencil", {"model": "openai/gpt-5"})
    data = json.loads(pencil.read_text())
    assert data["version"] == 1 and data["builtin_agents"][0]["id"] == "openprogram"
    engine.disconnect("openpencil")
    assert not pencil.exists()


@pytest.mark.parametrize("text", ['{"version": 2, "builtin_agents": []}', '{"theme": "dark"}',
                                  '{"version": 1, "builtin_agents": {}}', "[1]"])
def test_openpencil_leaves_alone_a_file_it_would_not_read(pencil, text) -> None:
    pencil.parent.mkdir(parents=True)
    pencil.write_text(text)
    with pytest.raises(engine.AgentConfigError):
        engine.connect("openpencil", {"model": "openai/gpt-5"})
    assert pencil.read_text() == text


def test_openpencil_refuses_while_it_runs(pencil, monkeypatch) -> None:
    from openprogram.models.gateway.agents import openpencil
    monkeypatch.setattr(openpencil, "running", lambda: True)
    with pytest.raises(engine.AgentConfigError, match="quit OpenPencil"):
        engine.connect("openpencil", {"model": "openai/gpt-5"})
    assert not pencil.exists()


def test_openpencil_model_cap(pencil, models) -> None:
    models([{"id": f"p/m{i}", "name": f"m{i}"} for i in range(70)])
    engine.connect("openpencil", {"model": "p/m0"})
    assert len(json.loads(pencil.read_text())["builtin_agents"][0]["models"]) == 64


def test_openpencil_dir_per_os(monkeypatch, tmp_path) -> None:
    from openprogram.models.gateway.agents import openpencil
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert openpencil.home("darwin") == Path(tmp_path) / "Library" / "Application Support" / "openpencil"
    assert openpencil.home("win32") == tmp_path / "Roaming" / "openpencil"
    assert openpencil.home("linux") == tmp_path / "xdg" / "openpencil"
