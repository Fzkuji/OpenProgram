"""Hermes Agent, Reasonix Studio and PenguinHarness on the gateway, after
yetone/magpie's ``hermes_test.go``, ``reasonix_test.go``,
``reasonix_studio_test.go`` and ``penguin_test.go`` (MIT)."""
from __future__ import annotations

import json
import struct
import tomllib
from pathlib import Path

import pytest
import yaml

from openprogram.models.gateway import edit

MODELS = [
    {"id": "deepseek/pro", "name": "Pro", "provider": "deepseek", "context_window": 128000, "max_tokens": 384000,
     "vision": False, "reasoning": True, "thinking_levels": ["off", "high", "max"]},
    {"id": "deepseek/flash", "name": "flash", "provider": "deepseek", "context_window": 0, "max_tokens": 0,
     "vision": True, "reasoning": False, "thinking_levels": []},
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("HERMES_HOME", "REASONIX_HOME", "PENGUIN_HOME", "PENGUIN_PROJECT_ID", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    from openprogram.models.gateway import address, conversion
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def registry(monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import hermes, penguin, reasonix
    monkeypatch.setattr(agents, "all_agents", lambda: [hermes.agent(), reasonix.agent(), penguin.agent()])


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# Hermes

def test_hermes_connect_and_disconnect(home, registry) -> None:
    from openprogram.models.gateway.agents import engine
    path = write(home / ".hermes" / "config.yaml",
                 "# mine\nmodel:\n  default: anthropic/claude-opus-5 # main\n  provider: openrouter\n"
                 "  base_url: https://openrouter.ai/api/v1\nproviders:\n  mine:\n    base_url: https://x\n")
    before = path.read_text()
    engine.connect("hermes", {"model": "deepseek/pro"})
    raw = path.read_text()
    doc = yaml.safe_load(raw)
    ours = doc["providers"]["openprogram"]
    assert doc["model"] == {"default": "deepseek/pro", "provider": "openprogram", "base_url": "https://openrouter.ai/api/v1"}
    assert doc["providers"]["mine"] and "# mine" in raw
    assert ours["api_mode"] == "chat_completions" and ours["base_url"] == "http://127.0.0.1:18100/gateway/openai/v1"
    assert ours["models"] == ["deepseek/pro", "deepseek/flash"] and ours["extra_headers"] == {"User-Agent": "hermes-agent"}
    assert ours["name"] == "openprogram"

    # Hermes writes keys of its own into the entry (`hermes model` sets default_model): they stay
    path.write_text(raw.replace("    api_mode: chat_completions", "    api_mode: chat_completions\n    default_model: deepseek/pro"))
    assert engine.status("hermes")["config"]["drift"] == []
    engine.disconnect("hermes")
    doc = yaml.safe_load(path.read_text())
    assert doc["model"] == {"default": "anthropic/claude-opus-5", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1"}
    # OpenProgram's entry goes whole, what Hermes wrote into it too
    assert "openprogram" not in doc["providers"]
    path.write_text(before)

    # a config with no provider of its own: none comes back
    write(path, "agent:\n  max_turns: 60\n")
    engine.connect("hermes", {"model": "deepseek/pro"})
    engine.disconnect("hermes")
    assert yaml.safe_load(path.read_text()) == {"agent": {"max_turns": 60}}


def test_hermes_keeps_the_users_api_mode_and_key_reference(home, registry) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    path = write(home / ".hermes" / "config.yaml",
                 "providers:\n  openprogram:\n    name: Mine\n    api_mode: anthropic\n    api_key: ${OP_KEY}\n")
    engine.connect("hermes", {"model": "deepseek/pro"})
    ours = yaml.safe_load(path.read_text())["providers"]["openprogram"]
    assert ours["api_mode"] == "anthropic_messages" and ours["base_url"] == "http://127.0.0.1:18100/gateway/anthropic"
    assert ours["api_key"] == "${OP_KEY}" and ours["name"] == "Mine"
    assert edit.load("dotenv", (path.parent / ".env").read_text()) == {"OP_KEY": token_for("hermes")}
    engine.disconnect("hermes")
    assert not (path.parent / ".env").exists()


def test_hermes_profiles(home, registry) -> None:
    from openprogram.models.gateway.agents import engine, hermes
    root = home / ".hermes"
    main = write(root / "config.yaml", "model:\n  default: kimi-k3\n  provider: moonshot\n")
    coder = write(root / "profiles" / "coder" / "config.yaml", "model:\n  default: gpt-9\n  provider: openai\n")
    bot = write(root / "profiles" / "u5c0f-u52a9-u624b" / "config.yaml", "model:\n  default: x\n")
    write(root / "profiles" / "old" / "config.yaml", "model:\n  default: x\n")
    write(root / "profiles" / ".deleted" / "old", "")
    write(root / "profiles" / "coder" / "profile.yaml", "display_name: Coder Bot\n")

    # the sticky profile is the one connected (active_profile)
    for sticky, want in [("coder\n", coder.parent), ("﻿coder", coder.parent), ("default\n", root),
                         ("", root), ("gone\n", root), ("old\n", root), ("../x\n", root)]:
        write(root / "active_profile", sticky)
        assert hermes.active() == want, sticky
    (root / "active_profile").unlink()

    agent = hermes.agent()
    assert [(f.key, f.label, f.quiet) for f in agent.fields] == [
        ("model", "Model", False), ("effort", "Effort", True), ("profile:coder", "Coder Bot", True),
        ("profile:u5c0f-u52a9-u624b", "小助手", True)]
    assert hermes.agent().id == "hermes"

    engine.connect("hermes", {"model": "deepseek/pro", "profile:coder": "deepseek/flash"})
    assert yaml.safe_load(coder.read_text())["model"] == {"default": "deepseek/flash", "provider": "openprogram"}
    other = yaml.safe_load(bot.read_text())
    assert other["model"] == {"default": "x"} and other["providers"]["openprogram"]["models"]
    assert yaml.safe_load(main.read_text())["model"]["provider"] == "openprogram"

    engine.disconnect("hermes")
    assert yaml.safe_load(coder.read_text()) == {"model": {"default": "gpt-9", "provider": "openai"}}
    assert yaml.safe_load(bot.read_text()) == {"model": {"default": "x"}}
    assert yaml.safe_load(main.read_text()) == {"model": {"default": "kimi-k3", "provider": "moonshot"}}


def test_hermes_root_of_a_custom_home(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import hermes
    own = home / "elsewhere" / "profiles" / "work"
    monkeypatch.setenv("HERMES_HOME", str(own))
    assert hermes.root() == home / "elsewhere" and hermes.active() == own
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes" / "profiles" / "x"))
    assert hermes.root() == home / ".hermes"
    assert hermes.unslug("test-u673a-u5668-u4eba") == "test机器人" and hermes.unslug("u0041") == "u0041"


# Reasonix

REASONIX = """# keep this comment
default_model = "native/old"

[[providers]]
name = "native"
kind = "openai"
base_url = "https://example.com/v1"
api_key_env = "NATIVE_API_KEY"
models = ["old", "other"]

[desktop]
provider_access = ["native"]
"""


def test_reasonix_gateway_and_restore(home, registry) -> None:
    from openprogram.models.gateway.agents import agent_of_ua, engine, reasonix
    from openprogram.models.gateway.keys import token_for
    path = write(home / ".reasonix" / "config.toml", REASONIX)
    env = write(home / ".reasonix" / ".env", "# original key\nNATIVE_API_KEY=native-secret\n")
    engine.connect("reasonix", {"model": "deepseek/flash", "planner": "deepseek/pro"})
    cfg = tomllib.loads(path.read_text())
    assert cfg["default_model"] == "openprogram/deepseek/flash"
    assert cfg["agent"] == {"planner_model": "openprogram/deepseek/pro"}
    assert cfg["providers"][0] == tomllib.loads(REASONIX)["providers"][0]
    ours = cfg["providers"][1]
    assert ours["name"] == "openprogram" and ours["kind"] == "openai" and ours["default"] == "deepseek/flash"
    assert ours["base_url"] == "http://127.0.0.1:18100/gateway/openai/v1" and ours["no_proxy"] is True
    assert ours["model_overrides"]["deepseek/pro"] == {"supported_efforts": ["high", "max"], "context_window": 128000,
                                                        "max_output_tokens": 128000}
    assert ours["model_overrides"]["deepseek/flash"] == {"reasoning_protocol": "none", "vision": True}
    assert cfg["desktop"]["provider_access"] == ["native", "openprogram"]
    assert edit.load("dotenv", env.read_text())[ours["api_key_env"]] == token_for("reasonix")
    assert agent_of_ua("reasonix/2.24.0") == "reasonix"
    assert engine.status("reasonix")["config"]["drift"] == []

    engine.disconnect("reasonix")
    assert path.read_text() == REASONIX
    assert env.read_text() == "# original key\nNATIVE_API_KEY=native-secret\n"


def test_reasonix_refuses_a_provider_it_does_not_own(home, registry) -> None:
    from openprogram.models.gateway.agents import engine
    path = write(home / ".reasonix" / "config.toml",
                 '[[providers]]\nname = "openprogram"\nkind = "openai"\napi_key_env = "MINE"\n')
    before = path.read_text()
    with pytest.raises(engine.AgentConfigError):
        engine.connect("reasonix", {"model": "deepseek/pro"})
    assert path.read_text() == before


def test_reasonix_without_config_makes_and_removes_it(home, registry) -> None:
    from openprogram.models.gateway.agents import engine, reasonix
    engine.connect("reasonix", {"model": "deepseek/pro"})
    assert tomllib.loads(reasonix.config_path().read_text())["providers"][0]["default"] == "deepseek/pro"
    engine.disconnect("reasonix")
    assert not reasonix.config_path().exists() and not reasonix.env_path().exists()


def _asar(name: str, version: str) -> bytes:
    metadata = json.dumps({"name": name, "version": version}).encode()
    header = json.dumps({"files": {"package.json": {"size": len(metadata), "offset": "0"}}}).encode()
    header_size = (8 + len(header) + 3) & ~3
    archive = bytearray(8 + header_size)
    struct.pack_into("<IIII", archive, 0, 4, header_size, header_size - 4, len(header))
    archive[16:16 + len(header)] = header
    return bytes(archive) + metadata


@pytest.mark.parametrize("name,version,want", [
    ("reasonix-studio-electron", "2.24.0", True), ("reasonix-studio-electron", "2.25.0-beta.1", True),
    ("reasonix-studio-electron", "1.21.3", False), ("some-other-app", "2.24.0", False)])
def test_reasonix_studio_package(tmp_path, name, version, want) -> None:
    from openprogram.models.gateway.agents.reasonix import studio_package
    path = tmp_path / "app.asar"
    path.write_bytes(_asar(name, version))
    assert studio_package(path) is want


def test_reasonix_studio_rejects_malformed_archives(tmp_path) -> None:
    from openprogram.models.gateway.agents.reasonix import studio_package
    for data in (b"", b"not an archive", bytes(16), bytes([4, 0, 0, 0, 255, 255, 255, 255]),
                 _asar("reasonix-studio-electron", "2.24.0")[:20]):
        path = tmp_path / "app.asar"
        path.write_bytes(data)
        assert not studio_package(path)


# PenguinHarness

PENGUIN = """default_model = { provider = "custom", model_id = "my-own" }

[providers.custom]
base_url = "https://example.com/v1"
client_type = "openai-chat"

[[models]]
provider = "custom"
model_id = "my-own"
api_key = "k1"
"""


def test_penguin(home, registry) -> None:
    from openprogram.models.gateway.agents import engine, penguin
    from openprogram.models.gateway.keys import token_for
    agent = penguin.agent()
    path = home / ".penguin" / "data" / "default_project" / ".project_config.toml"
    assert agent.path() == path and agent.config_dir() == home / ".penguin"
    write(path, PENGUIN)
    engine.connect("penguin", {"model": "deepseek/pro"})
    raw = path.read_text()
    assert raw.startswith('default_model = { provider = "openprogram", model_id = "deepseek/pro" }\n')
    cfg = tomllib.loads(raw)
    assert cfg["providers"]["openprogram"] == {"base_url": "http://127.0.0.1:18100/gateway/openai/v1",
                                               "client_type": "openai-responses", "api_key": token_for("penguin")}
    mine = [m for m in cfg["models"] if m["provider"] != "openprogram"]
    ours = [m for m in cfg["models"] if m["provider"] == "openprogram"]
    assert mine == [{"provider": "custom", "model_id": "my-own", "api_key": "k1"}]
    assert ours[0] == {"provider": "openprogram", "model_id": "deepseek/pro", "display_name": "Pro",
                       "context_window": 128000, "vision": False, "max_tokens": 128000}
    assert ours[1] == {"provider": "openprogram", "model_id": "deepseek/flash", "display_name": "flash", "vision": True}
    assert engine.status("penguin")["config"]["drift"] == []
    engine.disconnect("penguin")
    assert path.read_text() == PENGUIN


def test_penguin_root_and_project(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import penguin
    own = home / "own"
    monkeypatch.setenv("PENGUIN_HOME", str(own))
    monkeypatch.setenv("PENGUIN_PROJECT_ID", "work")
    assert penguin.config_path() == own / "work" / ".project_config.toml" and penguin.agent().config_dir() == home
    for bad in ("..", "../x", "a\\b", " "):
        monkeypatch.setenv("PENGUIN_PROJECT_ID", bad)
        assert penguin.config_path() == own / "default_project" / ".project_config.toml"


def test_penguin_without_config(home, registry) -> None:
    from openprogram.models.gateway.agents import engine, penguin
    engine.connect("penguin", {"model": "deepseek/flash"})
    assert tomllib.loads(penguin.config_path().read_text())["default_model"]["model_id"] == "deepseek/flash"
    engine.disconnect("penguin")
    assert not penguin.config_path().exists()


# Round 2: what magpie does that was skipped before

CLAUDE = {"id": "anthropic/claude-x", "name": "Claude X", "provider": "anthropic", "context_window": 200000,
          "max_tokens": 64000, "vision": True, "reasoning": True, "thinking_levels": ["low", "high"],
          "api": "anthropic-messages",
          "price": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}}


@pytest.fixture
def with_claude(monkeypatch):
    from openprogram.models.gateway import conversion
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in [*MODELS, CLAUDE]])


def test_hermes_anthropic_entry_for_messages_models(home, registry, with_claude) -> None:
    from openprogram.models.gateway.agents import engine
    path = write(home / ".hermes" / "config.yaml", "model:\n  default: kimi-k3\n  provider: moonshot\n")
    engine.connect("hermes", {"model": "anthropic/claude-x"})
    doc = yaml.safe_load(path.read_text())
    chat, messages = doc["providers"]["openprogram"], doc["providers"]["openprogram-anthropic"]
    assert chat["models"] == ["deepseek/pro", "deepseek/flash"] and chat["api_mode"] == "chat_completions"
    assert messages["models"] == ["anthropic/claude-x"] and messages["api_mode"] == "anthropic_messages"
    assert messages["base_url"] == "http://127.0.0.1:18100/gateway/anthropic" and messages["api_key"] == chat["api_key"]
    assert messages["name"] == "openprogram-anthropic"
    assert doc["model"] == {"default": "anthropic/claude-x", "provider": "openprogram-anthropic"}

    engine.connect("hermes", {"model": "deepseek/pro"})
    assert yaml.safe_load(path.read_text())["model"] == {"default": "deepseek/pro", "provider": "openprogram"}

    # an api_mode the user gave openprogram serves every model as they set
    path.write_text(path.read_text().replace("api_mode: chat_completions", "api_mode: codex_responses"))
    engine.connect("hermes", {"model": "anthropic/claude-x"})
    doc = yaml.safe_load(path.read_text())
    assert "openprogram-anthropic" not in doc["providers"]
    assert doc["providers"]["openprogram"]["models"] == ["deepseek/pro", "deepseek/flash", "anthropic/claude-x"]
    assert doc["model"]["provider"] == "openprogram"

    engine.disconnect("hermes")
    doc = yaml.safe_load(path.read_text())
    assert doc["model"] == {"default": "kimi-k3", "provider": "moonshot"}
    assert "openprogram-anthropic" not in (doc.get("providers") or {})


def test_hermes_effort(home, registry) -> None:
    from openprogram.models.gateway.agents import engine, hermes
    path = write(home / ".hermes" / "config.yaml", "agent:\n  max_turns: 60\n")
    effort = next(f for f in hermes.agent().fields if f.key == "effort")
    assert [v for v, _ in effort.options()] == ["none", "minimal", "low", "medium", "high", "xhigh"]
    engine.connect("hermes", {"model": "deepseek/pro", "effort": "high"})
    assert yaml.safe_load(path.read_text())["agent"] == {"max_turns": 60, "reasoning_effort": "high"}
    engine.connect("hermes", {"model": "deepseek/pro"})
    assert yaml.safe_load(path.read_text())["agent"] == {"max_turns": 60}
    engine.connect("hermes", {"model": "deepseek/pro", "effort": "none"})
    engine.disconnect("hermes")
    assert yaml.safe_load(path.read_text()) == {"agent": {"max_turns": 60}}


def test_hermes_bots_follow_the_model_and_come_back(home, registry) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine
    root = home / ".hermes"
    main = write(root / "config.yaml",
                 "model:\n  default: kimi-k3\n  provider: moonshot\nproviders:\n  moonshot:\n    base_url: https://m\n")
    engine.connect("hermes", {"model": "deepseek/pro"})
    entries = yaml.safe_load(main.read_text())["providers"]
    # bots made while connected: Hermes seeds each with a copy of the connected config
    seeded = {"model": {"default": "deepseek/pro", "provider": "openprogram"},
              "providers": {"openprogram": entries["openprogram"]}}
    copy = write(root / "profiles" / "copy" / "config.yaml", yaml.safe_dump(seeded))
    own = write(root / "profiles" / "own" / "config.yaml",
                yaml.safe_dump({**seeded, "model": {"default": "deepseek/flash", "provider": "openprogram"}}))
    mine = write(root / "profiles" / "mine" / "config.yaml", "model:\n  default: gpt-9\n  provider: openai\n")
    empty = write(root / "profiles" / "empty" / "config.yaml", "agent:\n  max_turns: 3\n")

    # a bot still on the model OpenProgram had set follows; one on another stays
    engine.connect("hermes", {"model": "deepseek/flash"})
    engine.connect("hermes", {"model": "deepseek/pro"})
    assert yaml.safe_load(copy.read_text())["model"] == {"default": "deepseek/pro", "provider": "openprogram"}
    assert yaml.safe_load(own.read_text())["model"] == {"default": "deepseek/pro", "provider": "openprogram"}
    assert yaml.safe_load(mine.read_text())["model"] == {"default": "gpt-9", "provider": "openai"}
    # one with no model at all gets the pick, as Hermes seeds one
    assert yaml.safe_load(empty.read_text())["model"] == {"default": "deepseek/pro", "provider": "openprogram"}

    # disconnecting moves each bot on OpenProgram's entries onto the user's model, with its provider
    engine.disconnect("hermes")
    assert yaml.safe_load(main.read_text())["model"] == {"default": "kimi-k3", "provider": "moonshot"}
    doc = yaml.safe_load(copy.read_text())
    assert doc["model"] == {"default": "kimi-k3", "provider": "moonshot"}
    assert doc["providers"] == {"moonshot": {"base_url": "https://m"}}
    assert yaml.safe_load(mine.read_text()) == {"model": {"default": "gpt-9", "provider": "openai"}}
    assert yaml.safe_load(empty.read_text()) == {"agent": {"max_turns": 3}}
    assert stash.agents() == [] and str(copy) in stash.entry("hermes#off")

    # connecting again puts back the bots nothing changed since
    doc = yaml.safe_load(own.read_text())
    doc["model"] = {"default": "glm-5", "provider": "zai"}
    own.write_text(yaml.safe_dump(doc))
    engine.connect("hermes", {"model": "deepseek/flash"})
    assert yaml.safe_load(copy.read_text())["model"] == {"default": "deepseek/pro", "provider": "openprogram"}
    assert yaml.safe_load(own.read_text())["model"] == {"default": "glm-5", "provider": "zai"}
    assert stash.entry("hermes#off") is None
    engine.disconnect("hermes")


def test_hermes_through_a_local_proxy(home, registry, with_claude) -> None:
    from openprogram.models.gateway.agents import engine, hermes
    path = write(home / ".hermes" / "config.yaml", "model:\n  default: x\n")
    with pytest.raises(engine.AgentConfigError):
        hermes.through("http://127.0.0.1:8787/bili/")
    engine.connect("hermes", {"model": "deepseek/pro"})
    hermes.through("http://127.0.0.1:8787/bili/")
    proxied = {"openprogram": "http://127.0.0.1:8787/bili/http://127.0.0.1:18100/gateway/openai/v1",
               "openprogram-anthropic": "http://127.0.0.1:8787/bili/http://127.0.0.1:18100/gateway/anthropic"}
    providers = yaml.safe_load(path.read_text())["providers"]
    assert {name: providers[name]["base_url"] for name in proxied} == proxied
    # a pick keeps the prefix
    engine.connect("hermes", {"model": "anthropic/claude-x"})
    providers = yaml.safe_load(path.read_text())["providers"]
    assert {name: providers[name]["base_url"] for name in proxied} == proxied
    hermes.through("")
    assert yaml.safe_load(path.read_text())["providers"]["openprogram"]["base_url"] == \
        "http://127.0.0.1:18100/gateway/openai/v1"
    # a prefix that isn't a local route is not one
    assert hermes.prefix({"providers": {"openprogram": {
        "base_url": "https://proxy.example/x/http://127.0.0.1:18100/gateway/openai/v1"}}},
        engine.wiring(hermes.agent(), {"model": "deepseek/pro"})) == ""
    engine.disconnect("hermes")
    assert yaml.safe_load(path.read_text()) == {"model": {"default": "x"}}


def test_reasonix_keeps_providers_the_user_added(home, registry) -> None:
    from openprogram.models.gateway.agents import engine
    path = write(home / ".reasonix" / "config.toml", REASONIX)
    engine.connect("reasonix", {"model": "deepseek/pro"})
    path.write_text(path.read_text() + '\n[[providers]]\nname = "later"\nkind = "openai"\n')
    engine.connect("reasonix", {"model": "deepseek/flash"})
    # OpenProgram's entry is rewritten where it is (magpie SetTOMLArrayTable)
    names = [p["name"] for p in tomllib.loads(path.read_text())["providers"]]
    assert names == ["native", "openprogram", "later"]
    assert path.read_text().endswith('\n[[providers]]\nname = "later"\nkind = "openai"\n')
    engine.disconnect("reasonix")
    cfg = tomllib.loads(path.read_text())
    assert [p["name"] for p in cfg["providers"]] == ["native", "later"]
    assert cfg["default_model"] == "native/old" and cfg["desktop"]["provider_access"] == ["native"]


def test_reasonix_effort(home, registry) -> None:
    from openprogram.models.gateway.agents import engine, reasonix
    path = write(home / ".reasonix" / "config.toml", REASONIX)
    assert reasonix.effort_choices({"model": "deepseek/pro"}) == ["auto", "high", "max"]
    assert reasonix.effort_choices({"model": "deepseek/flash"}) == []
    engine.connect("reasonix", {"model": "deepseek/pro", "effort": "max"})
    assert tomllib.loads(path.read_text())["providers"][1]["effort"] == "max"
    engine.connect("reasonix", {"model": "deepseek/pro", "effort": "auto"})
    assert "effort" not in tomllib.loads(path.read_text())["providers"][1]
    engine.disconnect("reasonix")
    assert path.read_text() == REASONIX


@pytest.mark.parametrize("display,version,want", [
    ("Reasonix Studio", "2.24.0", True), ("Reasonix Studio 2.24.0", "v2.25.1", True),
    ("Reasonix Studio", "1.21.3", False), ("Reasonix", "2.24.0", False), ("Reasonix Studios", "2.0.0", False)])
def test_reasonix_studio_release(display, version, want) -> None:
    from openprogram.models.gateway.agents.reasonix import studio_release
    assert studio_release(display, version) is want


def test_penguin_prices_vision_model_and_user_models(home, registry, with_claude) -> None:
    from openprogram.models.gateway.agents import engine, penguin
    path = write(penguin.config_path(), PENGUIN)
    engine.connect("penguin", {"model": "anthropic/claude-x"})
    ours = {m["model_id"]: m for m in tomllib.loads(path.read_text())["models"] if m["provider"] == "openprogram"}
    assert ours["anthropic/claude-x"]["pricing"] == {"unit": "usd_per_mtok", "cache_read": 0.3,
                                                     "cache_write": 3.0, "output": 15.0}
    assert "pricing" not in ours["deepseek/pro"]

    # Penguin picks one of OpenProgram's as its vision model, and the user adds a model of their own
    path.write_text('vision_model = { provider = "openprogram", model_id = "deepseek/flash" }\n' + path.read_text()
                    + '\n[[models]]\nprovider = "custom"\nmodel_id = "later"\n')
    engine.disconnect("penguin")
    cfg = tomllib.loads(path.read_text())
    assert "vision_model" not in cfg
    assert [m["model_id"] for m in cfg["models"]] == ["my-own", "later"]
    assert cfg["default_model"] == {"provider": "custom", "model_id": "my-own"} and "openprogram" not in cfg["providers"]

    # a vision model of the user's own stays
    path.write_text('vision_model = { provider = "custom", model_id = "my-own" }\n' + PENGUIN)
    engine.connect("penguin", {"model": "deepseek/pro"})
    engine.disconnect("penguin")
    assert tomllib.loads(path.read_text())["vision_model"] == {"provider": "custom", "model_id": "my-own"}


def test_penguin_new_config_is_owner_only(home, registry) -> None:
    from openprogram.models.gateway.agents import engine, penguin
    engine.connect("penguin", {"model": "deepseek/pro"})
    assert penguin.config_path().stat().st_mode & 0o777 == 0o600
    engine.disconnect("penguin")
