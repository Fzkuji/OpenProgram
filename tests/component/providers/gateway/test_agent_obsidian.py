"""Obsidian (Copilot for Obsidian), after yetone/magpie ``internal/agent/obsidian_test.go`` (MIT License)."""
from __future__ import annotations

import json
import sys

import pytest

from openprogram.models.gateway.agents import engine

from .conftest import MODELS

#: No key in the vault's file: the address names Obsidian alone (Agent.path_named).
def BASE() -> str:
    return "http://127.0.0.1:18100/gateway/agent/obsidian/openai/v1"

USER = {
    "settingsVersion": 4,
    "providers": {"mine": {"providerId": "mine", "providerType": "openai-compatible", "baseUrl": "http://x/v1"}},
    "configuredModels": [{"configuredModelId": "m-1", "providerId": "mine", "info": {"id": "x"}}],
    "backends": {"chat": {"enabledModels": ["m-1"]}, "embedding": {"enabledModels": []}},
    "defaultModelKey": "m-1",
}


@pytest.fixture
def obsidian(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import obsidian as module
    monkeypatch.setenv("XDG_CONFIG_HOME", str(agent_home / ".config"))
    monkeypatch.setenv("APPDATA", str(agent_home / "AppData" / "Roaming"))
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    return module


def _vault(module, vault_id, folder, version, data):
    listing = module.config_dirs()[0] / "obsidian.json"
    listing.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(listing.read_text()) if listing.exists() else {"vaults": {}}
    doc["vaults"][vault_id] = {"path": str(folder), "ts": 1, "open": True}
    listing.write_text(json.dumps(doc))
    folder.mkdir(parents=True, exist_ok=True)
    plugin = module.plugin_dir(folder)
    if version:
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text(json.dumps({"id": "copilot", "version": version}))
        if data is not None:
            (plugin / "data.json").write_text(data)
    return plugin / "data.json"


def test_obsidian_copilot(obsidian, agent_home, models) -> None:
    assert not obsidian.agent().installed()
    _vault(obsidian, "0000", agent_home / "NoAI", "", None)
    assert not obsidian.agent().installed()  # a vault without Copilot: nothing to set
    text = json.dumps(USER, indent=2) + "\n"
    path = _vault(obsidian, "a1b2", agent_home / "Notes", "4.0.19", text)
    assert obsidian.agent().installed()
    assert obsidian.agent().path() == obsidian.config_dirs()[0] / "obsidian.json"

    engine.connect("obsidian", {"model": "deepseek/deepseek-chat"})
    data = json.loads(path.read_text())
    provider = data["providers"]["openprogram"]
    assert provider["baseUrl"] == BASE() and provider["requiresApiKey"] is False and provider["origin"] == {"kind": "byok"}
    ours = [m for m in data["configuredModels"] if m["providerId"] == "openprogram"]
    ds, gpt = ours
    assert data["configuredModels"][0]["configuredModelId"] == "m-1"
    assert ds["configuredModelId"] == obsidian.model_id("deepseek/deepseek-chat")
    assert ds["info"] == {"id": "deepseek/deepseek-chat", "displayName": "DeepSeek Chat",
                          "modalities": {"input": ["text"], "output": ["text"]},
                          "limits": {"context": 128000, "output": 8192},
                          "reasoning": True, "reasoningEfforts": ["low", "medium", "high"]}
    assert gpt["info"]["modalities"]["input"] == ["text", "image"] and "limits" not in gpt["info"]
    assert data["backends"]["chat"]["enabledModels"] == ["m-1", ds["configuredModelId"], gpt["configuredModelId"]]
    assert data["backends"]["embedding"]["enabledModels"] == []
    assert data["defaultModelKey"] == ds["configuredModelId"]
    assert engine.status("obsidian")["config"]["drift"] == []

    # the user turns one off; a sync keeps it off, a new model comes on, a gone one goes
    data["backends"]["chat"]["enabledModels"].remove(gpt["configuredModelId"])
    path.write_text(json.dumps(data, indent=2))
    models([MODELS[0], {"id": "later/m1", "name": "m1"}])
    engine.sync("obsidian")
    data = json.loads(path.read_text())
    later = obsidian.model_id("later/m1")
    assert data["backends"]["chat"]["enabledModels"] == ["m-1", ds["configuredModelId"], later]
    assert [m["configuredModelId"] for m in data["configuredModels"]] == ["m-1", ds["configuredModelId"], later]
    assert next(m for m in data["configuredModels"] if m["configuredModelId"] == ds["configuredModelId"])[
        "configuredAt"] == ds["configuredAt"]

    # the enabled list is the user's to curate: theirs turned on there stays, only OpenProgram's ids go
    data["backends"]["chat"]["enabledModels"] = [ds["configuredModelId"], "m-1", later, "m-2"]
    path.write_text(json.dumps(data, indent=2))
    assert engine.status("obsidian")["config"]["drift"] == []
    engine.disconnect("obsidian")
    back = json.loads(path.read_text())
    assert "openprogram" not in back["providers"] and back["configuredModels"] == USER["configuredModels"]
    assert back["backends"]["chat"]["enabledModels"] == ["m-1", "m-2"]
    assert back["defaultModelKey"] == "m-1"


def test_obsidian_restores_untouched_vault(obsidian, agent_home) -> None:
    text = json.dumps(USER, indent=2) + "\n"
    path = _vault(obsidian, "a1b2", agent_home / "Notes", "4.0.19", text)
    engine.connect("obsidian", {"model": "openai/gpt-5"})
    engine.disconnect("obsidian")
    assert json.loads(path.read_text()) == USER


def test_obsidian_no_settings_yet(obsidian, agent_home) -> None:
    path = _vault(obsidian, "a1b2", agent_home / "Notes", "4.1.0", None)
    engine.connect("obsidian", {"model": "openai/gpt-5"})
    assert json.loads(path.read_text())["providers"]["openprogram"]["baseUrl"] == BASE()
    engine.disconnect("obsidian")
    assert not path.exists()


def test_obsidian_refuses(obsidian, agent_home) -> None:
    hand = dict(USER, providers={"openprogram": {"providerId": "openprogram", "baseUrl": "http://elsewhere/v1"}})
    path = _vault(obsidian, "a1b2", agent_home / "Notes", "4.0.19", json.dumps(hand))
    with pytest.raises(engine.AgentConfigError, match="of its own named openprogram"):
        engine.connect("obsidian", {"model": "openai/gpt-5"})
    assert json.loads(path.read_text()) == hand
    old = _vault(obsidian, "b2c3", agent_home / "Old", "3.2.0", "{}")
    path.unlink()
    (path.parent / "manifest.json").unlink()
    with pytest.raises(engine.AgentConfigError, match="Copilot 3"):
        engine.connect("obsidian", {"model": "openai/gpt-5"})
    assert old.read_text() == "{}"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux and macOS folders")
def test_obsidian_dirs(obsidian, agent_home) -> None:
    dirs = obsidian.config_dirs()
    if sys.platform == "darwin":
        assert dirs == [agent_home / "Library" / "Application Support" / "obsidian"]
    else:
        assert dirs[0] == agent_home / ".config" / "obsidian" and len(dirs) == 3
