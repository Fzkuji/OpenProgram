"""Grok Build, Kigi, Grok App and Raycast on the gateway, after yetone/magpie's
``grok_test.go``, ``kigi_test.go``, ``grokapp_test.go`` and ``raycast_test.go``
(MIT License, Copyright (c) yetone)."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from openprogram.models.gateway import edit

MODELS = [
    {"id": "deepseek/pro", "name": "Pro", "provider": "deepseek", "context_window": 1000000,
     "max_tokens": 8192, "thinking_levels": ["low", "high", "xhigh"], "api": "openai-completions"},
    {"id": "deepseek/flash", "name": "Flash", "provider": "deepseek", "context_window": 200000},
    {"id": "resp/gpt-6-sol", "name": "Sol", "provider": "resp", "api": "openai-responses", "vision": True},
    {"id": "anth/claude-sonnet-5", "name": "Sonnet", "provider": "anth", "api": "anthropic-messages"},
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("GROK_HOME", "KIGI_SHARE_DIR", "GROK_APP_HOME", "XDG_DATA_HOME", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import grok, grok_app, kigi, raycast
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    monkeypatch.setattr(agents, "all_agents",
                        lambda: [grok.agent(), kigi.agent(), grok_app.agent(), raycast.agent()])
    return tmp_path


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


GROK_USER = ('# mine\n[ui]\ntheme = "dark"\n\n[model.my-own]\nmodel = "x"\nbase_url = "https://x/v1"\n\n'
             '[models]\ndefault = "grok-4.6"\ndefault_reasoning_effort = "max"\n')


def test_grok(home, monkeypatch) -> None:
    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import engine, find
    from openprogram.models.gateway.keys import token_for
    monkeypatch.setenv("GROK_HOME", str(home / "grok"))
    path = home / "grok" / "config.toml"
    path.parent.mkdir()
    path.write_text(GROK_USER)
    assert find("grok-build").id == "grok" and find("grok").path() == path and find("grok").installed()

    engine.connect("grok", {"model": "deepseek/pro"})
    raw = path.read_text()
    doc = tomllib.loads(raw)
    pro = doc["model"]["openprogram/deepseek/pro"]
    assert pro["model"] == "deepseek/pro" and pro["api_key"] == token_for("grok")
    assert pro["base_url"] == "http://127.0.0.1:18100/gateway/openai/v1" and pro["api_backend"] == "chat_completions"
    assert pro["context_window"] == 1000000 and pro["reasoning_efforts"] == ["low", "high", "xhigh"]
    # each model asked on its own API, all on one gateway
    assert doc["model"]["openprogram/resp/gpt-6-sol"]["api_backend"] == "responses"
    sonnet = doc["model"]["openprogram/anth/claude-sonnet-5"]
    assert sonnet["api_backend"] == "messages" and sonnet["base_url"] == "http://127.0.0.1:18100/gateway/anthropic/v1"
    # a campaign of xAI's would set the default over it
    assert doc["models"]["default"] == "openprogram/deepseek/pro" and doc["features"]["campaigns"] is False
    # an effort the model lacks moves to its nearest
    assert doc["models"]["default_reasoning_effort"] == "xhigh"
    assert "# mine" in raw and "[model.my-own]" in raw and doc["ui"]["theme"] == "dark"
    assert raw.count('[model."openprogram/deepseek/pro"]') == 1

    # a window set by hand stays through a sync; a model removed leaves the list
    path.write_text(raw.replace("context_window = 200000", "context_window = 150000"))
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS if m["id"] != "resp/gpt-6-sol"])
    assert engine.sync("grok")
    doc = _toml(path)
    assert "openprogram/resp/gpt-6-sol" not in doc["model"]
    assert doc["model"]["openprogram/deepseek/flash"]["context_window"] == 150000
    assert path.read_text().count('[model."openprogram/deepseek/pro"]') == 1
    assert not engine.status("grok")["config"]["drift"]

    engine.disconnect("grok")
    doc = _toml(path)
    assert doc == tomllib.loads(GROK_USER)
    assert "openprogram" not in path.read_text() and "campaigns" not in path.read_text()


def test_kigi_is_named_as_itself(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import agent_of_ua, engine, find
    assert find("kigi").path() == home / ".kigi" / "config.toml"
    assert agent_of_ua("kigi/0.1.22 (macos; arm64)") == "kigi" and agent_of_ua("grok-shell/1.0") == "grok"
    monkeypatch.setenv("KIGI_SHARE_DIR", str(home / "share"))
    engine.connect("kigi", {"model": "deepseek/flash"})
    doc = _toml(home / "share" / "config.toml")
    assert doc["models"]["default"] == "openprogram/deepseek/flash" and doc["features"]["campaigns"] is False
    assert doc["model"]["openprogram/deepseek/flash"]["base_url"].endswith("/gateway/openai/v1")
    engine.disconnect("kigi")
    assert not (home / "share" / "config.toml").exists()


GROK_APP_USER = ('[models]\ndefault = "deepseek"\nmax_retries = 3\n\n'
                 '[model.deepseek]\nmodel = "deepseek-chat"\nbase_url = "https://api.deepseek.com/v1"\nname = "DeepSeek"\n')


def test_grok_app(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, find
    from openprogram.models.gateway.agents.grok_app import data_dir
    from openprogram.models.gateway.keys import token_for
    monkeypatch.setenv("GROK_APP_HOME", str(home / "app"))
    path = home / "app" / "agent-home" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(GROK_APP_USER)
    assert find("grok-app").id == "grok_app" and find("grok_app").path() == path

    engine.connect("grok_app", {"model": "deepseek/pro"})
    doc = _toml(path)
    # the route stays the App's
    assert doc["models"] == {"default": "deepseek", "max_retries": 3}
    channel = doc["model"]["openprogram"]
    assert channel["model"] == "deepseek/pro" and channel["base_url"] == "http://127.0.0.1:18100/gateway/openai/v1"
    assert channel["api_key"] == token_for("grok_app") and channel["api_backend"] == "chat_completions"
    assert channel["app_provider_mode"] == "generic" and channel["name"] == "OpenProgram"
    # Grok Build reads these two bare
    assert channel["context_window"] == 1000000 and channel["supports_reasoning_effort"] is True
    assert "app_supports_vision" not in channel
    models = json.loads(channel["app_models"])
    assert [m["id"] for m in models] == [m["id"] for m in MODELS]
    assert [(e["id"], e["isDefault"]) for e in models[0]["efforts"]] == [("low", False), ("high", True), ("xhigh", False)]
    assert models[2]["supportsVision"] is True and "contextWindow" not in models[2]

    # a default effort the user marked in the App stays through a sync
    models[0]["efforts"] = [dict(e, isDefault=e["id"] == "low") for e in models[0]["efforts"]]
    text = path.read_text()
    path.write_text(edit.edit("toml", text, sets={"model.openprogram.app_models": json.dumps(models)}))
    engine.sync("grok_app")
    kept = json.loads(_toml(path)["model"]["openprogram"]["app_models"])
    assert [e["id"] for e in kept[0]["efforts"] if e["isDefault"]] == ["low"]

    engine.disconnect("grok_app")
    assert _toml(path) == tomllib.loads(GROK_APP_USER)

    monkeypatch.delenv("GROK_APP_HOME")
    assert data_dir("darwin") == home / "Library" / "Application Support" / "com.grokapp.grok-app"
    assert data_dir("linux") == home / ".local" / "share" / "grok-app"
    # the App's own fallback, when it is the one there
    (home / ".grok-app").mkdir()
    assert data_dir("darwin") == home / ".grok-app" and data_dir("linux") == home / ".grok-app"


RAYCAST_USER = """# my Raycast providers
providers:
  - id: lmstudio
    name: LM Studio
    base_url: http://localhost:1234/v1
    models:
      - id: qwen/qwen3-14b
        name: "Qwen 3 14B"
        context: 32000

tools:
  web_search:
    provider: tavily
"""


def test_raycast_providers(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    from openprogram.models.gateway.keys import token_for
    agent = find("raycast-ai")
    path = home / ".config" / "raycast" / "ai" / "providers.yaml"
    assert agent.path() == path
    path.parent.mkdir(parents=True)
    path.write_text(RAYCAST_USER)
    assert agent.installed()
    user = yaml.safe_load(RAYCAST_USER)

    engine.connect("raycast", {"model": "deepseek/pro"})
    got = yaml.safe_load(path.read_text())
    assert got["providers"][0] == user["providers"][0] and got["tools"] == user["tools"]
    ours = got["providers"][1]
    assert ours["id"] == "openprogram" and ours["base_url"] == "http://127.0.0.1:18100/gateway/openai/v1"
    assert ours["api_keys"] == {"openprogram": token_for("raycast")}
    pro = ours["models"][0]
    assert pro["id"] == "deepseek/pro" and pro["context"] == 1000000
    assert pro["abilities"]["reasoning_effort"]["supported"] and not pro["abilities"]["vision"]["supported"]
    assert ours["models"][2]["abilities"]["vision"]["supported"] and "context" not in ours["models"][2]

    # a key the user added to OpenProgram's entry stays through a sync
    got["providers"][1]["include_user_email"] = True
    got["providers"][1]["models"][0]["description"] = "mine"
    path.write_text(yaml.safe_dump(got, sort_keys=False))
    engine.sync("raycast")
    again = yaml.safe_load(path.read_text())["providers"][1]
    assert again["include_user_email"] is True and again["models"][0]["description"] == "mine"

    # a provider added since: disconnecting takes out OpenProgram's entry alone
    again_doc = yaml.safe_load(path.read_text())
    again_doc["providers"].append({"id": "later", "name": "Later", "base_url": "http://x/v1"})
    path.write_text(yaml.safe_dump(again_doc, sort_keys=False))
    engine.disconnect("raycast")
    left = yaml.safe_load(path.read_text())
    assert [p["id"] for p in left["providers"]] == ["lmstudio", "later"] and left["tools"] == user["tools"]


def test_raycast_makes_and_removes_its_file(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    path = home / ".config" / "raycast" / "ai" / "providers.yaml"
    assert not find("raycast").installed() or (Path("/Applications/Raycast.app").exists())
    engine.connect("raycast", {"model": "deepseek/flash"})
    assert [p["id"] for p in yaml.safe_load(path.read_text())["providers"]] == ["openprogram"]
    engine.disconnect("raycast")
    assert not path.exists()


def test_raycast_providers_not_a_list(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".config" / "raycast" / "ai" / "providers.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("providers:\n  id: x\n")
    with pytest.raises(Exception, match="not a list"):
        engine.connect("raycast", {"model": "deepseek/pro"})
    assert path.read_text() == "providers:\n  id: x\n"


def test_grok_effort_and_compact_percent(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, find, grok
    from openprogram.state import settings
    monkeypatch.setattr(settings, "setting_value",
                        lambda key: 30 if key == "context.auto_compact_percent" else None)
    path = home / ".grok" / "config.toml"
    path.parent.mkdir()
    path.write_text(GROK_USER)
    from openprogram.models.gateway.agents import base
    agent = find("grok")
    connected = base.picked
    for model, levels in (("deepseek/pro", ["low", "high", "xhigh"]), ("deepseek/flash", ["low", "medium", "high"])):
        monkeypatch.setattr(base, "picked", lambda agent_id, model=model: {"model": model})
        assert [v for v, _ in agent.field("effort").options()] == levels
    monkeypatch.setattr(base, "picked", connected)
    assert grok.compact_percent(300000, 1000000) == 30 and grok.compact_percent(0, 1000000) == 0
    assert grok.compact_percent(1000000, 1000000) == 0 and grok.compact_percent(5, 1000000) == 1

    engine.connect("grok", {"model": "deepseek/pro", "effort": "high"})
    doc = _toml(path)
    assert doc["models"]["default_reasoning_effort"] == "high"
    # Grok compacts where OpenProgram would; no window, Grok's own 85%
    assert doc["model"]["openprogram/deepseek/pro"]["auto_compact_threshold_percent"] == 30
    assert "auto_compact_threshold_percent" not in doc["model"]["openprogram/anth/claude-sonnet-5"]

    # a percent the user set in a table stays; [session]'s, for every model, wins over OpenProgram's
    text = path.read_text().replace("auto_compact_threshold_percent = 30", "auto_compact_threshold_percent = 60", 1)
    path.write_text(text + '\n[session]\nauto_compact_threshold_percent = 70\n')
    engine.connect("grok", {"model": "deepseek/pro", "effort": "high"})
    doc = _toml(path)
    assert doc["model"]["openprogram/deepseek/pro"]["auto_compact_threshold_percent"] == 60
    assert "auto_compact_threshold_percent" not in doc["model"]["openprogram/deepseek/flash"]

    # "" is Grok's own again: the user's effort from before, nearest the model's
    engine.connect("grok", {"model": "deepseek/pro", "effort": ""})
    assert _toml(path)["models"]["default_reasoning_effort"] == "xhigh"
    engine.disconnect("grok")
    doc = _toml(path)
    assert doc["models"]["default_reasoning_effort"] == "max" and "openprogram" not in path.read_text()


def test_grok_app_puts_back_its_route(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    monkeypatch.setenv("GROK_APP_HOME", str(home / "app"))
    path = home / "app" / "agent-home" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(GROK_APP_USER)
    engine.connect("grok_app", {"model": "deepseek/pro"})
    # the user pressed Use on OpenProgram, and added a key to its channel
    text = path.read_text().replace('default = "deepseek"', 'default = "openprogram"')
    path.write_text(text + 'app_note = "mine"\n')
    engine.disconnect("grok_app")
    assert _toml(path) == tomllib.loads(GROK_APP_USER)
