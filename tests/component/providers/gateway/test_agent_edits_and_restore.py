"""What the user changes in an agent's own app keeps it OpenProgram's, and
disconnecting puts back what was there: provider blocks owned whole, list
entries' loose fields, lists that were there empty, QwenPaw's shared file and
key, and no gateway key in an agent's files (after yetone/magpie
``internal/agent``, MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import gateway_key, token_for


def _only(monkeypatch, *agents) -> None:
    from openprogram.models.gateway import agents as registry
    monkeypatch.setattr(registry, "all_agents", lambda: list(agents))


def _edit(path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2))


# Provider blocks owned whole (finding: plain sets left the keyed URL behind)

def test_fx_edit_inside_its_provider_keeps_it_owned_and_it_goes_whole(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import fx
    _only(monkeypatch, fx.agent())
    monkeypatch.setattr(fx, "fx_version", lambda: "0.0.20")
    path = agent_home / ".fx" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"provider": "gateway", "theme": "dark"}, indent=2))
    engine.connect("fx", {"model": "deepseek/deepseek-chat"})

    def edit(data):
        data["providers"]["openprogram"].update(reviewer_model="openai/gpt-5", tool_choice_mode="omit", mine=1)
    _edit(path, edit)
    assert engine.owned("fx") and engine.status("fx")["config"]["drift"] == []
    engine.sync("fx")
    ours = json.loads(path.read_text())["providers"]["openprogram"]
    assert ours["reviewer_model"] == "openai/gpt-5" and ours["tool_choice_mode"] == "omit" and ours["mine"] == 1
    engine.disconnect("fx")
    assert json.loads(path.read_text()) == {"provider": "gateway", "theme": "dark"}


def test_commandcode_edit_inside_its_provider_goes_whole(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import commandcode
    _only(monkeypatch, commandcode.agent())
    folder = agent_home / ".commandcode"
    folder.mkdir()
    providers = folder / "providers.json"
    providers.write_text('{"provider": {"mine": {"name": "Mine"}}}')
    engine.connect("commandcode", {"model": "openai/gpt-5"})
    _edit(providers, lambda data: data["provider"]["openprogram"].update(headers={"X": "1"}))
    assert engine.owned("commandcode")
    engine.disconnect("commandcode")
    assert json.loads(providers.read_text()) == {"provider": {"mine": {"name": "Mine"}}}


def test_jetbrains_hidden_model_keeps_it_owned_and_every_key_goes(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import copilot_jetbrains as jb
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    _only(monkeypatch, jb.agent())
    path = jb.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"other-api-key": "x"}, indent=2))
    engine.connect(jb.ID, {"model": "deepseek/deepseek-chat"})
    _edit(path, lambda data: data[jb.MODELS]["openai/gpt-5"].update(isRegistered=False))
    assert engine.owned(jb.ID) and engine.status(jb.ID)["config"]["drift"] == []
    _edit(path, lambda data: data[jb.MODELS]["openai/gpt-5"].update(deploymentUrl="http://elsewhere"))
    assert not engine.owned(jb.ID)
    engine.disconnect(jb.ID)
    assert json.loads(path.read_text()) == {"other-api-key": "x"}


def _obsidian_vault(agent_home, monkeypatch, data):
    from openprogram.models.gateway.agents import obsidian
    monkeypatch.setenv("XDG_CONFIG_HOME", str(agent_home / ".config"))
    monkeypatch.setenv("APPDATA", str(agent_home / "AppData" / "Roaming"))
    _only(monkeypatch, obsidian.agent())
    cfg = obsidian.config_dirs()[0]
    cfg.mkdir(parents=True)
    vault = agent_home / "vault"
    plugin = obsidian.plugin_dir(vault)
    plugin.mkdir(parents=True)
    (plugin / "manifest.json").write_text(json.dumps({"version": "4.0.19"}))
    (cfg / "obsidian.json").write_text(json.dumps({"vaults": {"a": {"path": str(vault)}}}))
    path = plugin / "data.json"
    path.write_text(json.dumps(data, indent=2))
    return obsidian, path


def test_obsidian_renamed_provider_owned_and_no_key_in_the_vault(agent_home, monkeypatch) -> None:
    before = {"providers": {}, "configuredModels": [], "backends": {"chat": {"enabledModels": []}},
              "defaultModelKey": "mine"}
    obsidian, path = _obsidian_vault(agent_home, monkeypatch, before)
    engine.connect("obsidian", {"model": "deepseek/deepseek-chat"})
    text = path.read_text()
    assert gateway_key() not in text and token_for("obsidian") not in text
    assert json.loads(text)["providers"]["openprogram"]["baseUrl"] == \
        "http://127.0.0.1:18100/gateway/agent/obsidian/openai/v1"
    _edit(path, lambda data: data["providers"]["openprogram"].update(displayName="My gateway"))
    assert engine.owned("obsidian") and engine.status("obsidian")["config"]["drift"] == []
    engine.disconnect("obsidian")
    # the provider goes, edited or not, and the lists that were there empty stay, empty
    assert json.loads(path.read_text()) == before


# Lists that were there empty come back empty

def test_astrbot_empty_lists_come_back_empty(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import astrbot
    monkeypatch.delenv("ASTRBOT_ROOT", raising=False)
    _only(monkeypatch, astrbot.agent())
    path = agent_home / ".astrbot" / "data" / "cmd_config.json"
    path.parent.mkdir(parents=True)
    before = {"provider_sources": [], "provider": [], "provider_settings": {"default_provider_id": ""}}
    path.write_text(json.dumps(before, indent=2))
    engine.connect("astrbot", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("astrbot")
    assert json.loads(path.read_text()) == before
    engine.connect("astrbot", {"model": "deepseek/deepseek-chat"})  # not "update AstrBot to 4.10"


def test_a_list_openprogram_added_goes_again(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import tuanjie_cowork
    _only(monkeypatch, tuanjie_cowork.agent())
    path = agent_home / ".codely" / "settings.json"
    path.parent.mkdir()
    path.write_text('{"customModels": []}')
    engine.connect("tuanjie-cowork", {"model": "openai/gpt-5"})
    engine.disconnect("tuanjie-cowork")
    assert json.loads(path.read_text()) == {"customModels": []}


# Members' loose fields

def test_workbuddy_effort_and_off_keep_it_owned(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import workbuddy
    monkeypatch.delenv("WORKBUDDY_CONFIG_DIR", raising=False)
    _only(monkeypatch, workbuddy.agent())
    path = agent_home / ".workbuddy" / "models.json"
    path.parent.mkdir()
    path.write_text("[]")
    engine.connect("workbuddy", {"model": "deepseek/deepseek-chat"})

    def edit(data):
        for entry in data:
            if entry["id"] == "deepseek/deepseek-chat":
                entry["reasoning"]["defaultEffort"] = "high"
            else:
                entry["disabled"] = True
    _edit(path, edit)
    assert engine.owned("workbuddy") and engine.status("workbuddy")["config"]["drift"] == []


def test_openpencil_name_and_on_off_keep_it_owned(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import openpencil
    _only(monkeypatch, openpencil.agent())
    monkeypatch.setattr(openpencil, "running", lambda: False)
    monkeypatch.setattr(openpencil, "home", lambda platform=None: agent_home / "openpencil")
    path = agent_home / "openpencil" / "settings.json"
    path.parent.mkdir()
    path.write_text('{"version": 1, "builtin_agents": []}')
    engine.connect("openpencil", {"model": "deepseek/deepseek-chat"})
    _edit(path, lambda data: data["builtin_agents"][0].update(display_name="Gateway", enabled=False))
    assert engine.owned("openpencil") and engine.status("openpencil")["config"]["drift"] == []
    engine.disconnect("openpencil")
    assert json.loads(path.read_text()) == {"version": 1, "builtin_agents": []}


def test_tuanjie_edits_in_the_app_keep_it_owned(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import tuanjie_cowork
    _only(monkeypatch, tuanjie_cowork.agent())
    path = agent_home / ".codely" / "settings.json"
    path.parent.mkdir()
    path.write_text("{}")
    engine.connect("tuanjie-cowork", {"model": "openai/gpt-5"})

    def edit(data):
        data["customModelProviders"][0]["name"] = "Gateway"
        data["customModels"][0].update(displayName="GPT", supportsMultimodal=False, roles=["model", "flash"])
    _edit(path, edit)
    assert engine.owned("tuanjie-cowork") and engine.status("tuanjie-cowork")["config"]["drift"] == []
    _edit(path, lambda data: data["customModelProviders"][0].update(baseUrl="http://elsewhere/v1"))
    assert not engine.owned("tuanjie-cowork")


# QwenPaw

@pytest.fixture
def qwenpaw(agent_home, monkeypatch):
    from openprogram.models.gateway.agents import qwenpaw as module
    monkeypatch.delenv("QWENPAW_WORKING_DIR", raising=False)
    monkeypatch.delenv("QWENPAW_SECRET_DIR", raising=False)
    work = agent_home / ".qwenpaw"
    profiles = {i: {"id": i, "workspace_dir": str(work / "workspaces" / i)} for i in ("default", "writer")}
    work.mkdir()
    (work / "config.json").write_text(json.dumps({"agents": {"agent_order": list(profiles), "profiles": profiles}}))
    for agent_id in profiles:
        folder = work / "workspaces" / agent_id
        folder.mkdir(parents=True)
        (folder / "agent.json").write_text(json.dumps({"active_model": {"provider_id": "x", "model": "y"}}))
    _only(monkeypatch, module.agent(), *module.extra_agents())
    return module


def test_qwenpaw_keeps_an_encrypted_key_only_while_it_is_the_one_written(qwenpaw) -> None:
    prov = qwenpaw.provider_path()
    prov.parent.mkdir(parents=True)
    prov.write_text(json.dumps({"id": "openprogram", "api_key": "ENC:another-instance"}))
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    assert json.loads(prov.read_text())["api_key"] == token_for("qwenpaw")
    # QwenPaw encrypts the key it was given: that one is kept
    _edit(prov, lambda data: data.update(api_key="ENC:ours"))
    engine.sync("qwenpaw")
    assert json.loads(prov.read_text())["api_key"] == "ENC:ours"
    engine.connect("qwenpaw#writer", {"model": "deepseek/deepseek-chat"})
    assert json.loads(prov.read_text())["api_key"] == "ENC:ours"


def test_qwenpaw_provider_stays_while_an_agent_names_it(qwenpaw, agent_home) -> None:
    prov = qwenpaw.provider_path()
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    # the writer agent is switched to OpenProgram's provider in QwenPaw itself
    writer = agent_home / ".qwenpaw" / "workspaces" / "writer" / "agent.json"
    writer.write_text(json.dumps({"active_model": {"provider_id": "x", "model": "y"},
                                  "fallback_models": [{"provider_id": "openprogram", "model": "openai/gpt-5"}]}))
    engine.disconnect("qwenpaw")
    assert prov.exists()
    default = agent_home / ".qwenpaw" / "workspaces" / "default" / "agent.json"
    assert json.loads(default.read_text())["active_model"] == {"provider_id": "x", "model": "y"}
    # the file left is still known as OpenProgram's: once no agent names it,
    # the next connect's last disconnect takes it out, key and all
    writer.write_text(json.dumps({"active_model": {"provider_id": "x", "model": "y"}}))
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("qwenpaw")
    assert not prov.exists()


def test_qwenpaw_left_file_gives_back_the_users_original(qwenpaw, agent_home) -> None:
    prov = qwenpaw.provider_path()
    prov.parent.mkdir(parents=True)
    original = json.dumps({"id": "openprogram", "base_url": "http://mine/v1", "api_key": "sk-mine"})
    prov.write_text(original)
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    writer = agent_home / ".qwenpaw" / "workspaces" / "writer" / "agent.json"
    writer.write_text(json.dumps({"active_model": {"provider_id": "openprogram", "model": "openai/gpt-5"}}))
    engine.disconnect("qwenpaw")
    assert json.loads(prov.read_text())["api_key"] == token_for("qwenpaw")  # still in use: left
    writer.write_text(json.dumps({"active_model": {"provider_id": "x", "model": "y"}}))
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("qwenpaw")
    assert prov.read_text() == original


def test_qwenpaw_provider_goes_once_no_agent_names_it(qwenpaw) -> None:
    prov = qwenpaw.provider_path()
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    engine.connect("qwenpaw#writer", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("qwenpaw")
    assert prov.exists()  # the writer's row still names it
    engine.disconnect("qwenpaw#writer")
    assert not prov.exists()


# No gateway key in an agent's files

@pytest.mark.parametrize("agent_id", ["qwen", "nanobot", "cline", "droid"])
def test_agents_are_given_a_key_of_their_own(agent_id) -> None:
    from openprogram.models.gateway.agents import find
    assert find(agent_id).named_key
