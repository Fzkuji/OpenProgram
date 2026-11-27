"""Disconnecting the agents in Pi's format and their kin leaves nothing of
OpenProgram's behind and the user's own picks as they are, after yetone/magpie
(``Disconnect``, ``piScopeWithout``, ``ompScopeWithout``,
``liveAgentUnwire``, ``liveAgentWired``, ``JSONToYAML``; MIT License,
Copyright (c) yetone)."""
from __future__ import annotations

import os
import stat

import pytest

from .test_pi_agents import _json, _write, home  # noqa: F401 - the fixture
from .test_pi_agents_kept import _serve


# 1. The provider block is OpenProgram's whole

@pytest.mark.parametrize("agent_id, rel", [
    ("pi", ".pi/agent/models.json"), ("stepcode", ".stepcode/models.json"), ("pencil", ".pencil/models.json"),
    ("mothx", ".mothx/settings.json"), ("aside", ".aside/u/0/models.json")])
def test_an_edited_provider_block_goes_on_disconnect(home, agent_id, rel) -> None:
    from openprogram.models.gateway.agents import engine
    for folder in (".stepcode", ".pencil", ".aside/u/0"):
        (home / folder).mkdir(parents=True, exist_ok=True)
    path = home / rel
    _write(path, {"providers": {"mine": {"baseUrl": "http://x"}}})
    engine.connect(agent_id, {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["providers"]["openprogram"]["headers"] = {"X-Mine": "1"}  # the user's edit inside it
    _write(path, data)
    engine.disconnect(agent_id)
    assert _json(path)["providers"] == {"mine": {"baseUrl": "http://x"}}


def test_omp_edited_provider_goes_on_disconnect(home) -> None:
    from openprogram.models.gateway.agents import engine
    folder = home / ".omp" / "agent"
    _write(folder / "config.yml", "modelRoles: {}\n")
    engine.connect("omp", {"model": "deepseek/deepseek-chat"})
    text = (folder / "models.yml").read_text().replace("  openprogram:\n", "  openprogram:\n    authHeader: true\n", 1)
    (folder / "models.yml").write_text(text)
    engine.disconnect("omp")
    assert not (folder / "models.yml").exists() or "openprogram" not in (folder / "models.yml").read_text()


# 2. No gateway key in a file others may read

@pytest.mark.parametrize("agent_id, rel", [
    ("pi", ".pi/agent/models.json"), ("omo", ".omo/agent/models.json"), ("omp", ".omp/agent/models.yml")])
def test_the_gateway_key_is_never_written(home, agent_id, rel) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import agent_of_key, gateway_key
    path = home / rel
    _write(path, "providers: {}\n" if rel.endswith("yml") else {"providers": {}})
    os.chmod(path, 0o644)
    if agent_id == "omp":
        _write(path.parent / "config.yml", "modelRoles: {}\n")
    engine.connect(agent_id, {"model": "deepseek/deepseek-chat"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o644 and gateway_key() not in path.read_text()
    from openprogram.models.gateway.edit import load
    block = load("yaml" if rel.endswith("yml") else "json", path.read_text())["providers"]["openprogram"]
    assert agent_of_key(block["apiKey"]) == agent_id


# 3. Picks the agent itself rewrites

@pytest.mark.parametrize("agent_id, rel", [
    ("pi", ".pi/agent/settings.json"), ("omo", ".omo/agent/settings.json"),
    ("prime-agent", ".prime/agent/settings.json"), ("mothx", ".mothx/settings.json")])
def test_the_agents_own_model_switch_is_no_takeover(home, agent_id, rel) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / rel
    _write(path, {"defaultProvider": "mine", "defaultModel": "x"})
    engine.connect(agent_id, {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["defaultModel"], data["defaultThinkingLevel"] = "openai/gpt-5.5", "high"  # its own /model
    _write(path, data)
    assert engine.owned(agent_id) and engine.status(agent_id)["config"]["drift"] == []
    # disconnecting while still on OpenProgram puts the pair back whole
    engine.disconnect(agent_id)
    data = _json(path)
    assert (data["defaultProvider"], data["defaultModel"]) == ("mine", "x")


def test_a_pick_moved_off_openprogram_stays(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".pi" / "agent" / "settings.json"
    _write(path, {"defaultProvider": "mine", "defaultModel": "x"})
    engine.connect("pi", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["defaultProvider"], data["defaultModel"] = "theirs", "y"
    _write(path, data)
    engine.disconnect("pi")
    data = _json(path)
    assert (data["defaultProvider"], data["defaultModel"]) == ("theirs", "y")


def test_mothx_sync_keeps_its_own_pick(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".mothx" / "settings.json"
    _write(path, {"defaultProvider": "mine", "defaultModel": "x", "providers": {"mine": {"baseUrl": "u"}}})
    engine.connect("mothx", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["defaultModel"] = "openai/gpt-5.5"  # MothX's own /model
    _write(path, data)
    _serve(monkeypatch, ["deepseek/deepseek-chat"])
    assert engine.sync("mothx")
    data = _json(path)
    assert data["defaultModel"] == "openai/gpt-5.5"
    assert [m["id"] for m in data["providers"]["openprogram"]["models"]] == ["deepseek/deepseek-chat"]


def test_aside_selection_goes_back_by_its_provider(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import aside, engine
    monkeypatch.setattr(aside, "repl", lambda account, expr: False)
    path = home / ".aside" / "u" / "0" / "settings.json"
    mine = {"provider": "mine", "modelId": "x", "thinkingLevel": "low"}
    _write(path, {"defaultModel": mine})
    engine.connect("aside", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["defaultModel"]["modelId"] = "openai/gpt-5.5"  # picked again in Aside
    _write(path, data)
    assert engine.owned("aside")
    engine.disconnect("aside")
    assert _json(path)["defaultModel"] == mine


def test_omp_role_moved_off_stays_and_one_on_goes_back(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import load
    folder = home / ".omp" / "agent"
    _write(folder / "config.yml", "modelRoles:\n  default: x/y\n  slow: x/z\n")
    engine.connect("omp", {"model": "deepseek/deepseek-chat", "slow": "openai/gpt-5.5"})
    text = (folder / "config.yml").read_text()
    text = text.replace("openprogram/deepseek/deepseek-chat", "openprogram/openai/gpt-5.5")
    text = text.replace("slow: openprogram/openai/gpt-5.5", "slow: mine/w")
    (folder / "config.yml").write_text(text)
    assert engine.owned("omp")
    engine.disconnect("omp")
    roles = load("yaml", (folder / "config.yml").read_text())["modelRoles"]
    assert roles == {"default": "x/y", "slow": "mine/w"}


def test_craft_default_switched_in_craft_is_no_takeover(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".craft-agent" / "config.json"
    _write(path, {"workspaces": [], "llmConnections": [{"slug": "mine", "name": "Mine"}],
                  "defaultLlmConnection": "mine"})
    engine.connect("craft-agents", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    ours = next(c for c in data["llmConnections"] if c["slug"] == "openprogram")
    ours["defaultModel"] = "openai/gpt-5.5"  # picked in Craft
    _write(path, data)
    assert engine.owned("craft-agents")
    engine.sync("craft-agents")
    ours = next(c for c in _json(path)["llmConnections"] if c["slug"] == "openprogram")
    assert ours["defaultModel"] == "openai/gpt-5.5"
    engine.disconnect("craft-agents")
    data = _json(path)
    assert data["defaultLlmConnection"] == "mine" and [c["slug"] for c in data["llmConnections"]] == ["mine"]


def test_liveagent_pick_on_another_of_its_providers_goes_back(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, liveagent
    monkeypatch.setattr(liveagent, "pids", lambda: "")
    path = home / ".liveagent" / "config.json"
    _write(path, {"providers": {
        "demo": {"name": "demo", "type": "codex", "baseUrl": "", "api": "openai-completions", "apiKey": "",
                 "models": [{"id": "model1"}]},
        "mine": {"name": "Mine", "type": "codex", "baseUrl": "http://x", "api": "openai-completions", "apiKey": "k",
                 "models": [{"id": "a"}]}},
        "defaultProvider": "demo", "defaultModel": "model1"})
    engine.connect("liveagent", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["defaultProvider"], data["defaultModel"] = "openprogram-responses", "openai/gpt-5.5"
    data["compactProvider"], data["compactModel"] = "openprogram", "deepseek/deepseek-chat"
    _write(path, data)
    assert engine.owned("liveagent")
    engine.disconnect("liveagent")
    data = _json(path)
    assert (data["defaultProvider"], data["defaultModel"]) == ("demo", "model1")
    # one it never wrote is reset as K-brain's own
    assert "compactProvider" not in data and data["compactModel"] == "model1"
    assert sorted(data["providers"]) == ["demo", "mine"]


# 4. enabledModels after a disconnect

@pytest.mark.parametrize("agent_id, rel, fmt", [
    ("pi", ".pi/agent/settings.json", "json"), ("stepcode", ".stepcode/config.toml", "toml")])
def test_edited_scope_loses_openprograms_entries(home, agent_id, rel, fmt) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import edit, load
    path = home / rel
    _write(path, edit(fmt, "", sets={"enabledModels": ["mine/x"]}))
    engine.connect(agent_id, {"model": "deepseek/deepseek-chat"})
    data = load(fmt, path.read_text())
    assert data["enabledModels"] == ["mine/x", "openprogram/deepseek/deepseek-chat"]
    path.write_text(edit(fmt, path.read_text(), sets={"enabledModels": [*data["enabledModels"], "mine/z"]}))
    engine.disconnect(agent_id)
    assert load(fmt, path.read_text())["enabledModels"] == ["mine/x", "mine/z"]


def test_omp_scope_loses_every_entry(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import load
    folder = home / ".omp" / "agent"
    _write(folder / "config.yml", "modelRoles: {}\nenabledModels:\n  - xai/grok\n")
    engine.connect("omp", {"model": "deepseek/deepseek-chat"})
    assert load("yaml", (folder / "config.yml").read_text())["enabledModels"] == ["xai/grok", "openprogram/**"]
    text = (folder / "config.yml").read_text().replace("  - xai/grok\n", "  - xai/grok\n  - mine/z\n")
    (folder / "config.yml").write_text(text)
    engine.disconnect("omp")
    assert load("yaml", (folder / "config.yml").read_text())["enabledModels"] == ["xai/grok", "mine/z"]


# 5. LiveAgent's providers all deleted in LiveAgent

def test_liveagent_sync_after_every_provider_deleted_writes_nothing(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, liveagent
    monkeypatch.setattr(liveagent, "pids", lambda: "")
    path = home / ".liveagent" / "config.json"
    _write(path, {"providers": {"mine": {"name": "Mine", "type": "codex", "baseUrl": "http://x",
                                         "api": "openai-completions", "apiKey": "k", "models": [{"id": "a"}]}},
                  "defaultProvider": "mine", "defaultModel": "a"})
    engine.connect("liveagent", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["providers"] = {k: v for k, v in data["providers"].items() if not k.startswith("openprogram")}
    _write(path, data)
    assert engine.owned("liveagent")
    assert engine.sync("liveagent") is False
    assert sorted(_json(path)["providers"]) == ["mine"]
    # connecting again here brings them back
    engine.connect("liveagent", {"model": "deepseek/deepseek-chat"})
    assert "openprogram" in _json(path)["providers"]


# 6. omp's models.json moved to models.yml is omp's own

def test_omp_moved_models_json_stays_the_users(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import load
    folder = home / ".omp" / "agent"
    _write(folder / "config.yml", "modelRoles:\n  default: x/y\n")
    _write(folder / "models.json", {"providers": {"mine": {"baseUrl": "http://x", "models": [{"id": "a"}]}},
                                    "equivalence": {"a": "b"}})
    engine.connect("omp", {"model": "deepseek/deepseek-chat"})
    # the user's own provider, edited since, is no sign of a takeover
    text = (folder / "models.yml").read_text().replace("baseUrl: http://x", "baseUrl: http://y")
    (folder / "models.yml").write_text(text)
    assert engine.owned("omp")
    _serve(monkeypatch, ["deepseek/deepseek-chat"])
    assert engine.sync("omp")
    engine.disconnect("omp")
    left = load("yaml", (folder / "models.yml").read_text())
    assert left == {"providers": {"mine": {"baseUrl": "http://y", "models": [{"id": "a"}]}}, "equivalence": {"a": "b"}}
    assert load("yaml", (folder / "config.yml").read_text())["modelRoles"] == {"default": "x/y"}


# The same, in the other agents

def test_other_agents_writing_a_key_into_their_files_name_themselves(home) -> None:
    from openprogram.models.gateway.agents import find
    for agent_id in ("pi", "omo", "omp", "minimax_code", "zcode", "air", "air_ide"):
        assert find(agent_id).named_key, agent_id


def test_nanobot_edited_provider_goes_whole(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.agents.nanobot import config_path
    path = config_path()
    _write(path, {"providers": {"mine": {"apiBase": "http://x"}}})
    engine.connect("nanobot", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["providers"]["openprogram"]["extraHeaders"]["X-Mine"] = "1"
    _write(path, data)
    engine.disconnect("nanobot")
    assert _json(path)["providers"] == {"mine": {"apiBase": "http://x"}}


# No agent is given the gateway key; one connected with it gets its own

def test_no_agent_is_given_the_gateway_key(home) -> None:
    from openprogram.models.gateway.agents import all_agents, engine
    from openprogram.models.gateway.keys import agent_of_key, gateway_key
    for agent in all_agents():
        key = engine.agent_key(agent)
        assert key != gateway_key() and agent_of_key(key), agent.id


def test_claude_code_files_hold_a_key_naming_it(home) -> None:
    from openprogram.models.gateway.agents import claude_code, engine
    from openprogram.models.gateway.keys import agent_of_key, gateway_key
    engine.connect("claude_code", {"model": "deepseek/deepseek-chat"})
    settings = _json(claude_code.config_path())
    assert agent_of_key(settings["env"]["ANTHROPIC_AUTH_TOKEN"]) == "claude_code"
    launch = claude_code.launch_settings("deepseek/deepseek-chat", None, "chat-1")
    assert gateway_key() not in launch.read_text()
    assert agent_of_key(_json(launch)["env"]["ANTHROPIC_AUTH_TOKEN"]) == "claude_code"


def test_pi_with_the_gateway_key_is_rewired_at_start(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, lifecycle
    from openprogram.models.gateway.keys import agent_of_key, gateway_key
    real = engine.agent_key
    monkeypatch.setattr(engine, "agent_key", lambda agent: gateway_key())
    engine.connect("pi", {"model": "deepseek/deepseek-chat"})
    monkeypatch.setattr(engine, "agent_key", real)
    path = home / ".pi" / "agent" / "models.json"
    assert _json(path)["providers"]["openprogram"]["apiKey"] == gateway_key()
    assert lifecycle.rewire() == ["Pi"]
    assert agent_of_key(_json(path)["providers"]["openprogram"]["apiKey"]) == "pi"
    assert lifecycle.rewire() == []
