"""AstrBot, after yetone/magpie ``internal/agent/astrbot_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

V1 = "http://127.0.0.1:18100/gateway/openai/v1"

CONFIG = """{
  "config_version": 3,
  "provider_sources": [
    {
      "id": "deepseek",
      "provider": "deepseek",
      "type": "openai_chat_completion",
      "provider_type": "chat_completion",
      "enable": true,
      "key": [
        "sk-user"
      ],
      "api_base": "https://api.deepseek.com/v1",
      "timeout": 120,
      "proxy": "",
      "custom_headers": {}
    }
  ],
  "provider": [
    {
      "id": "deepseek/deepseek-chat",
      "enable": true,
      "provider_source_id": "deepseek",
      "model": "deepseek-chat",
      "modalities": [
        "text",
        "tool_use"
      ],
      "custom_extra_body": {},
      "max_context_tokens": 0
    }
  ],
  "provider_settings": {
    "enable": true,
    "wake_prefix": ""
  },
  "agent_runner": {
    "runner_type": "local",
    "config": {
      "model": {
        "provider_id": "deepseek/deepseek-chat",
        "fallback_provider_ids": [],
        "request_max_retries": 5
      }
    }
  },
  "timezone": "Asia/Shanghai"
}"""


@pytest.fixture
def astrbot(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import astrbot as module
    monkeypatch.delenv("ASTRBOT_ROOT", raising=False)
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    path = agent_home / ".astrbot" / "data" / "cmd_config.json"
    path.parent.mkdir(parents=True)
    return path


def _read(path) -> dict:
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "AstrBot's BOM was lost"
    return json.loads(raw[3:])


def test_astrbot_connect_sync_disconnect(astrbot, models) -> None:
    astrbot.write_bytes(b"\xef\xbb\xbf" + CONFIG.encode())
    engine.connect("astrbot", {"model": "deepseek/deepseek-chat"})
    data = _read(astrbot)
    assert [s["id"] for s in data["provider_sources"]] == ["deepseek", "openprogram"]
    source = data["provider_sources"][1]
    assert source["api_base"] == V1 and source["key"] == [token_for("astrbot")]
    assert source["type"] == "openai_chat_completion" and source["timeout"] == 120
    listed = data["provider"]
    assert listed[0]["id"] == "deepseek/deepseek-chat"
    assert listed[1] == {"id": "openprogram/deepseek/deepseek-chat", "enable": True,
                         "provider_source_id": "openprogram", "model": "deepseek/deepseek-chat",
                         "modalities": ["text", "tool_use"], "custom_extra_body": {}, "max_context_tokens": 128000}
    assert listed[2]["modalities"] == ["text", "image", "tool_use"] and listed[2]["max_context_tokens"] == 0
    assert data["agent_runner"]["config"]["model"]["provider_id"] == "openprogram/deepseek/deepseek-chat"
    assert (astrbot.parent / "cmd_config.json.before-openprogram").exists()

    # what the user changed in OpenProgram's entries in the WebUI stays on sync
    data["provider_sources"][1]["timeout"] = 300
    data["provider"][1]["enable"] = False
    astrbot.write_bytes(b"\xef\xbb\xbf" + json.dumps(data, indent=2, ensure_ascii=False).encode())
    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    engine.connect("astrbot", {"model": "deepseek/deepseek-chat"})
    data = _read(astrbot)
    assert data["provider_sources"][1]["timeout"] == 300 and data["provider"][1]["enable"] is False
    assert data["provider"][-1]["id"] == "openprogram/later/m1"

    engine.disconnect("astrbot")
    back = _read(astrbot)
    assert back == json.loads(CONFIG)
    assert not (astrbot.parent / "cmd_config.json.before-openprogram").exists()


def test_astrbot_webui_edits_keep_it_owned_and_go_on_disconnect(astrbot, models, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    astrbot.write_bytes(b"\xef\xbb\xbf" + CONFIG.encode())
    engine.connect("astrbot", {"model": "deepseek/deepseek-chat"})
    data = _read(astrbot)
    data["provider_sources"][1]["timeout"] = 300          # a WebUI edit: loose
    data["provider"][1]["custom_extra_body"] = {"x": 1}
    astrbot.write_bytes(b"\xef\xbb\xbf" + json.dumps(data, indent=2).encode())
    assert engine.status("astrbot")["config"]["drift"] == [] and engine.owned("astrbot")

    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    assert lifecycle.resync() == ["astrbot"]
    assert _read(astrbot)["provider"][-1]["id"] == "openprogram/later/m1"
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["AstrBot"]
    data = _read(astrbot)
    assert data["provider_sources"][1]["api_base"].startswith("http://127.0.0.1:18200/")
    assert data["provider_sources"][1]["timeout"] == 300

    engine.disconnect("astrbot")
    back = _read(astrbot)
    assert [s["id"] for s in back["provider_sources"]] == ["deepseek"]
    assert [m["id"] for m in back["provider"]] == ["deepseek/deepseek-chat"]


def test_astrbot_before_agent_runner(astrbot) -> None:
    old = json.loads(CONFIG)
    del old["agent_runner"]
    old["provider_settings"]["default_provider_id"] = "deepseek/deepseek-chat"
    astrbot.write_text(json.dumps(old, indent=2))
    engine.connect("astrbot", {"model": "openai/gpt-5"})
    data = json.loads(astrbot.read_text())
    assert data["provider_settings"]["default_provider_id"] == "openprogram/openai/gpt-5" and "agent_runner" not in data
    engine.disconnect("astrbot")
    assert json.loads(astrbot.read_text()) == old


def test_astrbot_refuses_without_its_settings(astrbot) -> None:
    with pytest.raises(engine.AgentConfigError, match="start AstrBot once"):
        engine.connect("astrbot", {"model": "openai/gpt-5"})
    astrbot.write_text('{"config_version": 2}')
    with pytest.raises(engine.AgentConfigError, match="4.10"):
        engine.connect("astrbot", {"model": "openai/gpt-5"})
    assert astrbot.read_text() == '{"config_version": 2}'


def test_astrbot_root(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import astrbot as module
    monkeypatch.delenv("ASTRBOT_ROOT", raising=False)
    assert module.root() == agent_home / ".astrbot"
    (agent_home / "AstrBot").mkdir()
    (agent_home / "AstrBot" / ".astrbot").write_text("")
    assert module.root().name.lower() == "astrbot"  # ~/astrbot or ~/AstrBot (one folder on a case-blind disk)
    monkeypatch.setenv("ASTRBOT_ROOT", str(agent_home / "run"))
    assert module.config_path() == agent_home / "run" / "data" / "cmd_config.json"
