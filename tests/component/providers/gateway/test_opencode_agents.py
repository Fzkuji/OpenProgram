"""The OpenCode-format agents, after yetone/magpie's tests of the same agents:
``opencode_v2_test.go``, ``opencode_variants_test.go``, ``deveco_test.go``,
``openchamber_test.go``, ``air_test.go``, ``air_ide_test.go`` and
``openbot_test.go``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Request

from openprogram.models.gateway import edit

MODELS = [
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "context_window": 128000,
     "max_tokens": 8192, "thinking_levels": ["high", "low", "medium"], "reasoning": True},
    {"id": "openai/gpt-5.5", "name": "GPT-5.5", "provider": "openai", "context_window": 400000,
     "max_tokens": 128000, "vision": True, "thinking_levels": ["none", "high", "xhigh"], "input": ["text", "audio"],
     "price": {"input": 1.25, "output": 10.0, "cache_read": 0.125, "cache_write": 0}},
    {"id": "local/plain", "name": "Plain", "provider": "local"},
]


@pytest.fixture
def homes(tmp_path, monkeypatch):
    user = tmp_path / "home"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user / ".config"))
    for name in ("OPENCODE_CONFIG_DIR", "MIMOCODE_HOME", "DEVECO_CONFIG_DIR", "OPENCHAMBER_DATA_DIR", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway.agents import air, opencode
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    monkeypatch.setattr(opencode, "version", lambda: "1.3.9")
    monkeypatch.setattr(air, "find_tool", lambda name: "")
    return user


def _json(path: Path):
    return edit.load("jsonc", path.read_text())


# The block (agents.go magpieProviderJSONAt, openCodeVariants)

def test_variants_weakest_first_and_fast() -> None:
    from openprogram.models.gateway.agents.opencode_format import variant, variants
    assert list(variants(["max", "high", "low", "medium", "xhigh"])) == ["low", "medium", "high", "xhigh", "max"]
    assert variants([]) == {}
    assert list(variants(["low", "high"], fast=True)) == ["low", "high", "low-fast", "high-fast"]
    assert list(variants([], fast=True)) == ["fast"]
    assert variant("high-fast") == {"reasoningEffort": "high", "service_tier": "priority",
                                    "extraBody": {"service_tier": "priority"}}


def test_opencode1_keeps_its_words(homes) -> None:
    from openprogram.models.gateway.agents import engine
    path = homes / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"model": "anthropic/claude", "theme": "dark"}\n')
    engine.connect("opencode", {"model": "deepseek/deepseek-chat", "small": "openai/gpt-5.5"})
    doc = _json(path)
    assert "providers" not in doc and "agents" not in doc
    assert doc["model"] == "openprogram/deepseek/deepseek-chat"
    assert doc["small_model"] == "openprogram/openai/gpt-5.5"
    block = doc["provider"]["openprogram"]
    assert block["npm"] == "@ai-sdk/openai-compatible"
    assert block["options"]["baseURL"] == "http://127.0.0.1:18100/gateway/openai/v1"
    assert block["options"]["setCacheKey"] is True
    assert list(block["models"]) == [m["id"] for m in MODELS]  # the catalog's order
    chat = block["models"]["deepseek/deepseek-chat"]
    assert chat["reasoning"] is True and list(chat["variants"]) == ["low", "medium", "high"]
    assert chat["limit"] == {"context": 128000, "output": 8192}
    gpt = block["models"]["openai/gpt-5.5"]
    assert "reasoning" not in gpt  # its levels aren't low, medium and high
    assert gpt["modalities"] == {"input": ["text", "image", "audio"], "output": ["text"]}
    assert gpt["cost"] == {"input": 1.25, "output": 10.0, "cache_read": 0.125, "cache_write": 0}
    assert "cost" not in chat
    assert block["models"]["local/plain"] == {"name": "Plain", "variants": {}}
    engine.disconnect("opencode")
    assert _json(path) == {"model": "anthropic/claude", "theme": "dark"}


def test_opencode2_writes_its_own_words(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, opencode
    monkeypatch.setattr(opencode, "version", lambda: "2.0.4")  # one that has the list in its file
    path = homes / ".config" / "opencode" / "opencode.json"
    engine.connect("opencode", {"model": "deepseek/deepseek-chat", "small": "deepseek/deepseek-chat"})
    raw = path.read_text()
    doc = _json(path)
    assert "provider" not in doc and "small_model" not in doc and '"reasoning"' not in raw
    assert doc["agents"]["title"]["model"] == "openprogram/deepseek/deepseek-chat"
    block = doc["providers"]["openprogram"]
    assert block["package"] == "aisdk:@ai-sdk/openai-compatible"
    assert block["compatibility"] == {"supportsPromptCacheKey": True}
    assert "setCacheKey" not in block["settings"] and block["settings"]["apiKey"]
    chat = block["models"]["deepseek/deepseek-chat"]
    assert [v["id"] for v in chat["variants"]] == ["low", "medium", "high"]
    assert chat["variants"][0] == {"id": "low", "settings": {"reasoningEffort": "low"}}
    gpt = block["models"]["openai/gpt-5.5"]
    assert gpt["capabilities"] == {"tools": True, "input": ["text", "image", "audio"], "output": ["text"]}
    assert gpt["cost"] == [{"input": 1.25, "output": 10.0, "cache": {"read": 0.125, "write": 0}}]
    assert not (path.parent / "plugins").exists()
    engine.disconnect("opencode")
    assert not path.exists()


def test_opencode_keeps_what_the_user_added(homes) -> None:
    """A variant, a header and a setCacheKey of the user's on OpenProgram's
    provider stay through a reconnect; a variant as OpenProgram writes it is
    OpenProgram's (magpie openCodeKept)."""
    from openprogram.models.gateway.agents import engine
    path = homes / ".config" / "opencode" / "opencode.json"
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    doc = _json(path)
    block = doc["provider"]["openprogram"]
    block["options"]["headers"] = {"X-Mine": "1"}
    block["options"]["setCacheKey"] = False
    block["models"]["deepseek/deepseek-chat"]["variants"]["turbo"] = {"reasoningEffort": "low", "mine": True}
    path.write_text(json.dumps(doc, indent=2))
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    block = _json(path)["provider"]["openprogram"]
    assert block["options"]["headers"] == {"X-Mine": "1"} and block["options"]["setCacheKey"] is False
    assert list(block["models"]["deepseek/deepseek-chat"]["variants"]) == ["low", "medium", "high", "turbo"]


def test_a_sync_rewrites_the_list_not_the_picks(homes, monkeypatch) -> None:
    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import engine
    path = homes / ".config" / "opencode" / "opencode.json"
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    path.write_text(path.read_text().replace('"model": "openprogram/deepseek/deepseek-chat"', '"model": "mine/own"'))
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS[:1]])
    assert engine.sync("opencode")
    doc = _json(path)
    assert doc["model"] == "mine/own" and list(doc["provider"]["openprogram"]["models"]) == ["deepseek/deepseek-chat"]


def test_opencode_config_dir(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import opencode
    assert opencode.config_path() == homes / ".config" / "opencode" / "opencode.json"
    (homes / ".config" / "opencode").mkdir(parents=True)
    (homes / ".config" / "opencode" / "opencode.jsonc").write_text("{}")
    assert opencode.config_path().name == "opencode.jsonc"
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(homes / "oc"))
    assert opencode.config_path() == homes / "oc" / "opencode.json"


def test_opencode_two() -> None:
    from openprogram.models.gateway.agents.opencode_format import is_two, version_parts
    assert version_parts("v2.0.25") == (2, 0, 25) and version_parts("1.18") == (1, 18, 0)
    assert is_two("2.0.4") and not is_two("1.19.6") and not is_two("") and not is_two("dev")


# DevEco Code, MiMo Code (deveco_test.go)

def test_deveco_config_as_it_finds_it(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import deveco
    folder = homes / ".config" / "deveco"
    assert deveco.config_path() == folder / "deveco.json"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}")
    assert deveco.config_path() == folder / "config.json"
    (folder / "deveco.jsonc").write_text('{"$schema":"https://opencode.ai/config.json"}')
    assert deveco.config_path() == folder / "deveco.jsonc"
    monkeypatch.setenv("DEVECO_CONFIG_DIR", str(homes / "deveco-cfg"))
    assert deveco.config_path() == homes / "deveco-cfg" / "deveco.json"
    monkeypatch.setenv("DEVECO_CONFIG_DIR", "deveco-cfg")
    assert deveco.config_path() == folder / "deveco.jsonc"


def test_deveco_connect_keeps_the_users_own(homes) -> None:
    """Its key names it; the user's own provider, comments and $schema stay."""
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import gateway_key, token_for
    path = homes / ".config" / "deveco" / "deveco.jsonc"
    path.parent.mkdir(parents=True)
    original = ('{\n  "$schema": "https://opencode.ai/config.json",\n  // mine\n  "model": "deveco/glm-5",\n'
                '  "provider": {"mine": {"name": "mine", "options": {"apiKey": "sk-own"}}}\n}\n')
    path.write_text(original)
    engine.connect("deveco", {"model": "deepseek/deepseek-chat"})
    text = path.read_text()
    doc = _json(path)
    assert "// mine" in text and doc["$schema"] == "https://opencode.ai/config.json"
    assert doc["provider"]["openprogram"]["options"]["apiKey"] == token_for("deveco") != gateway_key()
    assert doc["provider"]["mine"]["options"]["apiKey"] == "sk-own"
    engine.disconnect("deveco")
    assert "// mine" in path.read_text() and _json(path) == edit.load("jsonc", original)


def test_mimocode_home(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import mimocode
    assert mimocode.config_path() == homes / ".config" / "mimocode" / "mimocode.json"
    monkeypatch.setenv("MIMOCODE_HOME", str(homes / "mimo"))
    assert mimocode.config_path() == homes / "mimo" / "config" / "mimocode.json"
    monkeypatch.setenv("MIMOCODE_HOME", "relative")
    assert mimocode.config_path() == homes / ".config" / "mimocode" / "mimocode.json"


# OpenChamber (openchamber_test.go)

def test_openchamber_model_and_shared_provider(homes) -> None:
    from openprogram.models.gateway.agents import engine
    chamber = homes / ".config" / "openchamber"
    chamber.mkdir(parents=True)
    prefs = chamber / "preferences.json"
    prefs.write_text(json.dumps({"version": 1, "fields": {"theme": {"value": "dark", "updatedAt": 1},
                                                          "defaultVariant": {"value": "high", "updatedAt": 1}}}))
    oc = homes / ".config" / "opencode" / "opencode.json"
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat"})
    fields = _json(prefs)["fields"]
    assert fields["defaultModel"]["value"] == "openprogram/deepseek/deepseek-chat"
    assert "smallModelUseDefault" not in fields and "defaultVariant" not in fields
    assert not (chamber / "settings.json").exists()  # only where it is
    assert "openprogram" in _json(oc)["provider"] and "model" not in _json(oc)

    # OpenCode on the same config: the provider is shared, and stays while
    # either is connected
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    engine.disconnect("openchamber")
    assert "openprogram" in _json(oc)["provider"]
    assert _json(prefs)["fields"] == {"theme": {"value": "dark", "updatedAt": 1},
                                      "defaultVariant": {"value": "high", "updatedAt": 1}}
    engine.disconnect("opencode")
    assert not oc.exists()


def test_openchamber_settings_only_and_bad_prefs(homes) -> None:
    from openprogram.models.gateway.agents import engine
    chamber = homes / ".config" / "openchamber"
    chamber.mkdir(parents=True)
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat", "small": "openai/gpt-5.5"})
    settings = _json(chamber / "settings.json")
    assert settings["defaultModel"] == "openprogram/deepseek/deepseek-chat"
    assert settings["smallModelOverride"] == "openprogram/openai/gpt-5.5"
    engine.disconnect("openchamber")
    (chamber / "preferences.json").write_text('{"version": 2}')
    with pytest.raises(engine.AgentConfigError):
        engine.connect("openchamber", {"model": "deepseek/deepseek-chat"})
    assert not (homes / ".config" / "opencode" / "opencode.json").exists()


# JetBrains Air (air_test.go, air_ide_test.go)

def test_air_adds_its_agent(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import air, engine
    monkeypatch.setattr(air, "find_tool", lambda name: "/opt/bin/opencode")
    acp = air.config_path()
    acp.parent.mkdir(parents=True)
    acp.write_text('{"agent_servers": {"Mine": {"command": "x"}}}')
    engine.connect("air", {"model": "deepseek/deepseek-chat"})
    oc = acp.parent / "openprogram-opencode.json"
    server = _json(acp)["agent_servers"]["OpenProgram"]
    assert server == {"command": "/opt/bin/opencode", "args": ["acp"], "env": {"OPENCODE_CONFIG": str(oc)}}
    doc = _json(oc)
    assert doc["enabled_providers"] == ["openprogram"] and doc["model"] == doc["small_model"]
    assert doc["$schema"] == "https://opencode.ai/config.json" and "openprogram" in doc["provider"]
    engine.disconnect("air")
    assert _json(acp) == {"agent_servers": {"Mine": {"command": "x"}}} and not oc.exists()


def test_air_finds_an_acp_registry_opencode(homes, monkeypatch) -> None:
    import sys
    from openprogram.models.gateway.agents import air
    monkeypatch.setattr(sys, "platform", "darwin")
    found = homes / "Library" / "Caches" / "JetBrains" / "IntelliJIdea2026.2" / "acp-agents" / "opencode" / "1.19.0"
    found.mkdir(parents=True)
    (found / "opencode").write_text("")
    assert air.air_opencode() == str(found / "opencode")
    assert "isn't installed" not in air.agent().notice()


def test_air_ide_detected_by_ide_version(homes, monkeypatch) -> None:
    import sys
    from openprogram.models.gateway.agents import air_ide
    monkeypatch.setattr(sys, "platform", "linux")
    assert not air_ide.agent().installed()
    (homes / ".config" / "JetBrains" / "PyCharm2026.1").mkdir(parents=True)
    assert not air_ide.agent().installed()
    (homes / ".config" / "JetBrains" / "IntelliJIdea2026.2").mkdir()
    assert air_ide.agent().installed()
    assert air_ide.config_path() == homes / ".jetbrains" / "acp.json"


# OpenBot (openbot_test.go)

def test_openbot_makes_and_removes_its_file(homes) -> None:
    from openprogram.models.gateway.agents import engine, openbot
    path = openbot.config_path()
    engine.connect("openbot", {"model": "deepseek/deepseek-chat"})
    doc = _json(path)
    assert doc["version"] == 1
    [entry] = doc["providers"]
    from openprogram.models.gateway.keys import path_key, token_for
    assert entry["baseUrl"] == f"http://127.0.0.1:18100/gateway/agent/{path_key(token_for('openbot'))}/openai/v1"
    assert entry["secret"] is None and entry["id"] == "openprogram"
    assert entry["models"][0] == {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "contextWindow": 128000}
    assert entry["models"][2] == {"id": "local/plain", "name": "Plain"}
    engine.disconnect("openbot")
    assert not path.exists()


def test_openbot_keeps_the_users_entries(homes) -> None:
    from openprogram.models.gateway.agents import engine, openbot
    path = openbot.config_path()
    path.parent.mkdir(parents=True)
    mine = {"id": "mine", "name": "Mine", "baseUrl": "http://x/v1", "models": [], "secret": "sealed"}
    path.write_text(json.dumps({"version": 1, "providers": [mine]}))
    engine.connect("openbot", {"model": "deepseek/deepseek-chat"})
    doc = _json(path)
    assert doc["providers"][0] == mine and doc["providers"][1]["id"] == "openprogram"
    # a name and secret the user gave OpenProgram's entry stay
    doc["providers"][1].update(name="Gateway", secret="theirs")
    path.write_text(json.dumps(doc))
    engine.connect("openbot", {"model": "openai/gpt-5.5"})
    ours = _json(path)["providers"][1]
    assert ours["name"] == "Gateway" and ours["secret"] == "theirs"
    engine.disconnect("openbot")
    assert _json(path)["providers"][0] == mine


def test_openbot_leaves_alone_a_file_it_cannot_read(homes) -> None:
    from openprogram.models.gateway.agents import engine, openbot
    path = openbot.config_path()
    path.parent.mkdir(parents=True)
    for text in ('{"version": 2, "providers": []}', '{"version": 1, "providers": {}}', "[1]"):
        path.write_text(text)
        with pytest.raises(engine.AgentConfigError):
            engine.connect("openbot", {"model": "deepseek/deepseek-chat"})
        assert path.read_text() == text


def test_openbot_models_it_takes(homes) -> None:
    from openprogram.models.gateway.agents import openbot
    from openprogram.models.gateway.agents.base import Wiring
    rows = [{"id": "p/ok", "name": "x" * 200, "context_window": 500},
            {"id": "p/has space", "name": "bad"},
            {"id": "p/ok", "name": "again"}]
    rows += [{"id": f"p/m{i}", "name": f"m{i}", "context_window": 200_000_000} for i in range(80)]
    at = Wiring(agent=openbot.agent(), values={"model": "p/ok"}, worker="http://127.0.0.1:1", key="k", models=rows)
    models = openbot.entry(at)["models"]
    assert len(models) == 64 and models[0] == {"id": "p/ok", "name": "p/ok"}
    assert all("contextWindow" not in m for m in models)


def test_openbot_carries_its_key_in_its_address(homes) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.models.gateway.agents import engine, openbot
    from openprogram.models.gateway.attribution import agent_of

    app = FastAPI()

    @app.get("/gateway/openai/v1/models")
    def models(request: Request):
        return {"agent": agent_of(request, "openai-chat")}

    owner = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                          bind_host="127.0.0.1", port=18100, allowed_origins=())
    guarded = OwnerAuthMiddleware(app, auth_state=owner)
    engine.connect("openbot", {"model": "deepseek/deepseek-chat"})
    base = _json(openbot.config_path())["providers"][0]["baseUrl"].removeprefix("http://127.0.0.1:18100")
    with TestClient(guarded, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        assert client.get(base + "/models").json() == {"agent": "openbot"}
        assert client.get("/gateway/agent/openbot/openai/v1/models").status_code == 401  # never keyless


def test_opencode_then_openchamber_share_the_users_original(homes) -> None:
    """The second agent on a shared key takes the first one's original, so the
    last to disconnect puts back the user's own, not the first's."""
    from openprogram.models.gateway.agents import engine
    oc = homes / ".config" / "opencode" / "opencode.json"
    oc.parent.mkdir(parents=True)
    oc.write_text('{"model": "mine/own"}\n')
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("opencode")
    doc = _json(oc)
    assert doc["model"] == "mine/own" and "openprogram" in doc["provider"]
    engine.disconnect("openchamber")
    assert _json(oc) == {"model": "mine/own"}


# OpenCode finds the list itself (opencode_discover_test.go)

def test_opencode_discovers_its_models(homes, monkeypatch) -> None:
    """An OpenCode that loads plugins gets OpenProgram's provider without its
    models and the plugin beside it; what the user said of a model stays."""
    from openprogram.models.gateway.agents import engine, opencode
    from openprogram.models.gateway.agents.opencode_format import PLUGIN_MARK
    path = homes / ".config" / "opencode" / "opencode.json"
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    doc = _json(path)
    # the user's reply limit on a model of OpenProgram's; a model of its from before
    doc["provider"]["openprogram"]["models"]["deepseek/deepseek-chat"]["limit"]["output"] = 4096
    doc["provider"]["openprogram"]["models"]["gone/model"] = {"name": "old"}
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(opencode, "version", lambda: "1.19.6")
    engine.sync("opencode")
    block = _json(path)["provider"]["openprogram"]
    # the model list leaves the file; the limit the user typed stays whole
    assert block["models"] == {"deepseek/deepseek-chat": {"limit": {"context": 128000, "output": 4096}}}
    assert "small_model" not in _json(path)
    plugin = path.parent / "plugins" / "openprogram-models.js"
    source = plugin.read_text()
    assert source.startswith(PLUGIN_MARK) and "__OPENPROGRAM__" not in source
    assert '"id": "openai/gpt-5.5"' in source and 'const ID = "openprogram"' in source
    engine.disconnect("opencode")
    assert not plugin.exists()


def test_a_plugin_of_the_users_own_is_left_alone(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, opencode
    monkeypatch.setattr(opencode, "version", lambda: "2.2.6")
    plugin = homes / ".config" / "opencode" / "plugins" / "openprogram-models.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// mine\n")
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    assert plugin.read_text() == "// mine\n"
    assert "models" not in _json(plugin.parent.parent / "opencode.json")["providers"]["openprogram"]
    engine.disconnect("opencode")
    assert plugin.read_text() == "// mine\n"


def test_discover_versions() -> None:
    from openprogram.models.gateway.agents.opencode_format import discovers
    assert discovers("1.4.0") and not discovers("1.3.9") and discovers("2.0.5") and not discovers("2.0.4")
    assert not discovers("") and not discovers("0.9.0")


def test_gateway_serves_the_opencode_list(homes) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from openprogram.interfaces.server.routes import gateway
    app = FastAPI()
    gateway.register(app)
    with TestClient(app) as client:
        one = client.get("/gateway/openai/v1/models?format=opencode").json()["models"]
        two = client.get("/gateway/openai/v1/models?format=opencode2").json()["models"]
        plain = client.get("/gateway/openai/v1/models").json()
    assert [m["id"] for m in one] == [m["id"] for m in MODELS]
    assert one[0]["entry"]["reasoning"] is True and "variants" in one[0]["entry"]
    assert isinstance(two[0]["entry"]["variants"], list) and "reasoning" not in two[0]["entry"]
    assert "data" in plain


def test_a_provider_of_the_users_own_on_the_gateway_is_named(homes) -> None:
    """A provider of the user's own already pointing at the gateway and
    listing the model is named, and no second list is written (magpie
    ownGatewayProvider)."""
    from openprogram.models.gateway.agents import engine
    path = homes / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True)
    mine = {"options": {"baseURL": "http://localhost:18100/gateway/openai/v1/"},
            "models": {"deepseek/deepseek-chat": {}}}
    path.write_text(json.dumps({"provider": {"ds": mine}}))
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    doc = _json(path)
    assert doc["model"] == "ds/deepseek/deepseek-chat" and "openprogram" not in doc["provider"]
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    doc = _json(path)
    assert doc["model"] == "openprogram/openai/gpt-5.5" and "openprogram" in doc["provider"]
    engine.disconnect("opencode")
    assert _json(path) == {"provider": {"ds": mine}}


def test_opencode_and_openchamber_share_the_plugin(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, opencode
    monkeypatch.setattr(opencode, "version", lambda: "2.2.6")
    plugin = homes / ".config" / "opencode" / "plugins" / "openprogram-models.js"
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    engine.connect("openchamber", {"model": "openai/gpt-5.5"})
    engine.disconnect("opencode")
    left = _json(plugin.parent.parent / "opencode.json")
    # the provider and plugin OpenChamber names stay; OpenCode's own model goes
    assert plugin.is_file() and "openprogram" in left["providers"] and "model" not in left
    engine.disconnect("openchamber")
    assert not plugin.exists() and not (plugin.parent.parent / "opencode.json").exists()


# What stays the user's, and what goes back as it was

def _oc(homes: Path) -> Path:
    return homes / ".config" / "opencode" / "opencode.json"


def test_a_header_added_in_openprograms_provider_is_kept_and_goes_with_it(homes) -> None:
    """OpenProgram's provider is its own object: a header the user adds in it
    is no drift, a sync keeps it, and disconnect takes the provider out whole."""
    from openprogram.models.gateway.agents import engine
    path = _oc(homes)
    path.parent.mkdir(parents=True)
    path.write_text('{"theme":"dark"}\n')
    engine.connect("opencode", {"model": "deepseek/deepseek-chat"})
    doc = _json(path)
    doc["provider"]["openprogram"]["options"]["headers"] = {"X-Mine": "1"}
    doc["provider"]["openprogram"]["models"]["openai/gpt-5.5"]["variants"]["turbo"] = {"mine": 1}
    path.write_text(json.dumps(doc))
    assert engine.status("opencode")["config"]["drift"] == [] and engine.owned("opencode")
    engine.sync("opencode")
    block = _json(path)["provider"]["openprogram"]
    assert block["options"]["headers"] == {"X-Mine": "1"}
    assert block["models"]["openai/gpt-5.5"]["variants"]["turbo"] == {"mine": 1}
    engine.disconnect("opencode")
    assert path.read_text() == '{"theme":"dark"}\n'


def test_a_model_picked_after_a_shared_disconnect_is_the_users(homes) -> None:
    """OpenCode back on after it went while OpenChamber stayed: its model's
    original is the one the user set since, not the one from before."""
    from openprogram.models.gateway.agents import engine
    path = _oc(homes)
    path.parent.mkdir(parents=True)
    path.write_text('{"model":"anthropic/x","theme":"dark"}\n')
    (homes / ".config" / "openchamber").mkdir(parents=True)
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("opencode")
    doc = _json(path)
    doc["model"] = "anthropic/new"
    path.write_text(json.dumps(doc))
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    engine.disconnect("opencode")
    assert _json(path)["model"] == "anthropic/new"
    engine.disconnect("openchamber")
    assert _json(path) == {"model": "anthropic/new", "theme": "dark"}


def test_moving_to_found_models_leaves_no_old_catalog_values(homes, monkeypatch) -> None:
    """The list leaving the file on the move to the plugin: a name or window
    the catalog has changed since is not taken for the user's."""
    import copy

    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import engine, opencode
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    new = copy.deepcopy(MODELS)
    new[0].update(context_window=64000, name="DeepSeek V4")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in new])
    monkeypatch.setattr(opencode, "version", lambda: "1.5.0")
    engine.sync("opencode")
    assert "models" not in _json(_oc(homes))["provider"]["openprogram"]
    assert engine.status("opencode")["config"]["drift"] == []


def test_the_move_to_opencode_2_carries_the_users_fields(homes, monkeypatch) -> None:
    """magpie openCodeFromV1: a header and a variant the user put in the
    provider under ``provider`` go along to the one under ``providers``."""
    from openprogram.models.gateway.agents import engine, opencode
    path = _oc(homes)
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    doc = _json(path)
    doc["provider"]["openprogram"]["options"]["headers"] = {"X-Mine": "1"}
    doc["provider"]["openprogram"]["models"]["openai/gpt-5.5"]["variants"]["turbo"] = {"mine": 1}
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(opencode, "version", lambda: "2.0.4")
    engine.sync("opencode")
    doc = _json(path)
    assert "provider" not in doc
    block = doc["providers"]["openprogram"]
    assert block["headers"] == {"X-Mine": "1"} and "headers" not in block["settings"]
    variants = block["models"]["openai/gpt-5.5"]["variants"]
    assert {"id": "turbo", "settings": {}, "body": {"mine": 1}} in variants
    assert [v["id"] for v in variants] == ["none", "high", "xhigh", "turbo"]


def test_a_reply_limit_the_user_typed_stays(homes, monkeypatch) -> None:
    """magpie outputsKept: a limit.output that is none OpenProgram wanted is the
    user's, kept by every sync and no drift; the others follow the catalog."""
    import copy

    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import engine
    path = _oc(homes)
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    doc = _json(path)
    doc["provider"]["openprogram"]["models"]["deepseek/deepseek-chat"]["limit"]["output"] = 4096
    path.write_text(json.dumps(doc))
    assert engine.status("opencode")["config"]["drift"] == []
    new = copy.deepcopy(MODELS)
    new[0]["max_tokens"] = 16384
    new[1]["max_tokens"] = 64000
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in new])
    engine.sync("opencode")
    models = _json(path)["provider"]["openprogram"]["models"]
    assert models["deepseek/deepseek-chat"]["limit"]["output"] == 4096
    assert models["openai/gpt-5.5"]["limit"]["output"] == 64000
    # a value OpenProgram wanted once is its own, not the user's
    doc = _json(path)
    doc["provider"]["openprogram"]["models"]["openai/gpt-5.5"]["limit"]["output"] = 128000
    path.write_text(json.dumps(doc))
    engine.sync("opencode")
    assert _json(path)["provider"]["openprogram"]["models"]["openai/gpt-5.5"]["limit"]["output"] == 64000


@pytest.mark.parametrize("version", ["1.3.9", "2.0.4"])
def test_a_small_model_unpicked_is_the_agents_own_again(homes, monkeypatch, version) -> None:
    from openprogram.models.gateway.agents import engine, opencode
    monkeypatch.setattr(opencode, "version", lambda: version)
    path = _oc(homes)
    path.parent.mkdir(parents=True)
    path.write_text('{"small_model":"anthropic/haiku"}\n')
    engine.connect("opencode", {"model": "openai/gpt-5.5", "small": "deepseek/deepseek-chat"})
    engine.connect("opencode", {"model": "openai/gpt-5.5"})
    doc = _json(path)
    assert "small_model" not in doc and "agents" not in doc
    assert engine.status("opencode")["config"]["drift"] == []
    engine.disconnect("opencode")
    assert path.read_text() == '{"small_model":"anthropic/haiku"}\n'


def test_openchamber_small_model_unpicked_goes(homes) -> None:
    from openprogram.models.gateway.agents import engine, openchamber
    openchamber.home().mkdir(parents=True)
    openchamber.prefs_path().write_text(json.dumps({"version": 1, "fields": {}}))
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat", "small": "openai/gpt-5.5"})
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat"})
    fields = json.loads(openchamber.prefs_path().read_text())["fields"]
    assert "smallModelUseDefault" not in fields and "smallModelOverride" not in fields
    assert fields["defaultModel"]["value"] == "openprogram/deepseek/deepseek-chat"
    engine.disconnect("openchamber")
    assert json.loads(openchamber.prefs_path().read_text()) == {"version": 1, "fields": {}}


def test_openchamber_keeps_the_variant_picked_for_the_same_model(homes) -> None:
    from openprogram.models.gateway.agents import engine, openchamber
    openchamber.home().mkdir(parents=True)
    openchamber.prefs_path().write_text(json.dumps({"version": 1, "fields": {}}))
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat"})
    doc = json.loads(openchamber.prefs_path().read_text())
    doc["fields"]["defaultVariant"] = {"value": "high", "updatedAt": 1}
    openchamber.prefs_path().write_text(json.dumps(doc))
    engine.connect("openchamber", {"model": "deepseek/deepseek-chat", "small": "openai/gpt-5.5"})
    fields = json.loads(openchamber.prefs_path().read_text())["fields"]
    assert fields["defaultVariant"] == {"value": "high", "updatedAt": 1}
    # another model: the variant was the old one's
    engine.connect("openchamber", {"model": "openai/gpt-5.5"})
    assert "defaultVariant" not in json.loads(openchamber.prefs_path().read_text())["fields"]


@pytest.mark.parametrize("version", ["1.5.0", "2.1.0"])
def test_shared_folders_made_go_with_the_last_agent(homes, monkeypatch, version) -> None:
    """The folders the first write made (opencode/, plugins/) are handed on
    with the file, whichever agent goes first."""
    from openprogram.models.gateway.agents import engine, opencode
    monkeypatch.setattr(opencode, "version", lambda: version)
    (homes / ".config" / "openchamber").mkdir(parents=True)
    for first, second in (("opencode", "openchamber"), ("openchamber", "opencode")):
        engine.connect("opencode", {"model": "openai/gpt-5.5"})
        engine.connect("openchamber", {"model": "deepseek/deepseek-chat"})
        engine.disconnect(first)
        engine.disconnect(second)
        assert not (homes / ".config" / "opencode").exists(), first


def test_files_go_back_byte_for_byte(homes, monkeypatch) -> None:
    """A disconnect that leaves the settings as they were leaves the user's
    own bytes: a one-line file stays on one line, keys in their order."""
    from openprogram.models.gateway.agents import engine, opencode
    path = homes / ".config" / "mimocode" / "mimocode.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"model":"anthropic/x","theme":"dark"}\n')
    engine.connect("mimocode", {"model": "openai/gpt-5.5"})
    engine.disconnect("mimocode")
    assert path.read_text() == '{"model":"anthropic/x","theme":"dark"}\n'
    monkeypatch.setattr(opencode, "version", lambda: "2.1.0")
    oc = _oc(homes)
    oc.parent.mkdir(parents=True)
    oc.write_text('{\n  "model": "anthropic/x",\n  "small_model": "anthropic/y"\n}\n')
    engine.connect("opencode", {"model": "openai/gpt-5.5", "small": "deepseek/deepseek-chat"})
    engine.disconnect("opencode")
    assert oc.read_text() == '{\n  "model": "anthropic/x",\n  "small_model": "anthropic/y"\n}\n'


def test_a_one_line_json_edit_stays_on_one_line() -> None:
    out = edit.edit("json", '{"a":1}\n', sets={"b": {"c": 2}})
    assert out == '{"b":{"c":2},"a":1}\n'
    assert edit.edit("json", out, deletes=["b"]) == '{"a":1}\n'
    assert edit.edit("json", "{}\n", sets={"b": 1}) == '{\n  "b": 1\n}\n'


def test_openbot_disconnect_leaves_a_file_openbot_reads(homes) -> None:
    """magpie openBotUnwire: the version stays beside the user's own
    endpoints; a file OpenProgram made goes once nothing else is in it; one
    that was there comes back as it was."""
    from openprogram.models.gateway.agents import engine, openbot
    path = openbot.config_path()
    engine.connect("openbot", {"model": "deepseek/deepseek-chat"})
    doc = json.loads(path.read_text())
    mine = {"id": "mine", "name": "Mine", "baseUrl": "http://x/v1", "models": [], "secret": None}
    doc["providers"].append(mine)
    path.write_text(json.dumps(doc))
    engine.disconnect("openbot")
    assert json.loads(path.read_text()) == {"version": 1, "providers": [mine]}
    path.unlink()
    engine.connect("openbot", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("openbot")
    assert not path.exists()
    path.write_text('{"version":1,"providers":[]}')
    engine.connect("openbot", {"model": "deepseek/deepseek-chat"})
    engine.disconnect("openbot")
    assert path.read_text() == '{"version":1,"providers":[]}'
