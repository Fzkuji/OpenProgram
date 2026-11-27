"""Qoder and Qoder CN, after yetone/magpie ``internal/agent/qoder_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def qoder(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import qoder as module
    made = [module.agent(), *module.extra_agents()]
    monkeypatch.setattr(agents, "all_agents", lambda: made)
    return agent_home


def test_qoder_connect_sync_and_restore(qoder, models, monkeypatch) -> None:
    path = qoder / ".qoder" / "settings.json"
    path.parent.mkdir()
    user = {"model": {"name": "qwen3-coder", "preferences": {"qwen3-coder": {"reasoning": {"effort": "high"}}}},
            "theme": "dark"}
    path.write_text(json.dumps(user, indent=2) + "\n")

    engine.connect("qoder", {"model": "deepseek/deepseek-chat"})
    data = json.loads(path.read_text())
    entry = data["providers"]["openprogram"]
    assert entry["protocol"] == "openai" and entry["baseUrl"] == V1 and entry["apiKey"] == token_for("qoder")
    assert entry["model"] == "deepseek/deepseek-chat"
    assert entry["models"][0] == {
        "model": "deepseek/deepseek-chat", "displayName": "DeepSeek Chat", "contextWindow": 128000,
        "maxOutputTokens": 8192, "capabilities": {"tools": True, "vision": False, "thinking": {
            "modes": ["enabled"], "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high"],
            "requiresBudgetForEnabled": False}}}
    assert entry["models"][1]["capabilities"] == {"tools": True, "vision": True}
    assert data["model"]["name"] == "openprogram/deepseek/deepseek-chat"
    # the effort in use goes with the pick
    assert data["model"]["preferences"]["openprogram/deepseek/deepseek-chat"] == {"reasoning": {"effort": "high"}}
    assert data["model"]["reasoningEffort"] == "high"

    # the user adds a timeout and names the provider: kept, no drift, sync and rewire go on
    data = json.loads(path.read_text())
    data["providers"]["openprogram"].update({"timeout": 60, "displayName": "Mine"})
    path.write_text(json.dumps(data, indent=2))
    assert engine.status("qoder")["config"]["drift"] == [] and engine.owned("qoder")
    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    assert engine.sync("qoder")
    entry = json.loads(path.read_text())["providers"]["openprogram"]
    assert entry["models"][-1]["model"] == "later/m1" and entry["timeout"] == 60 and entry["displayName"] == "Mine"
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["Qoder"]
    entry = json.loads(path.read_text())["providers"]["openprogram"]
    assert entry["baseUrl"].startswith("http://127.0.0.1:18200/") and entry["timeout"] == 60

    # edited inside, it still goes whole
    data = json.loads(path.read_text())
    data["providers"]["openprogram"]["protocol"] = "anthropic"
    path.write_text(json.dumps(data, indent=2))
    engine.disconnect("qoder")
    assert json.loads(path.read_text()) == user


def test_qoder_cn_is_its_own_row(qoder, monkeypatch) -> None:
    from openprogram.models.gateway.agents import find
    monkeypatch.setenv("QODERCN_CONFIG_DIR", str(qoder / "cn"))
    assert find("qoder-cn").path() == qoder / "cn" / "settings.json" and find("qodercn").id == "qoder-cn"
    engine.connect("qoder-cn", {"model": "openai/gpt-5"})
    entry = json.loads((qoder / "cn" / "settings.json").read_text())["providers"]["openprogram"]
    assert entry["apiKey"] == token_for("qoder-cn")
    engine.disconnect("qoder-cn")
    assert not (qoder / "cn" / "settings.json").exists()


def test_qoder_plugin_settings_are_no_sign_of_the_cli(qoder, monkeypatch) -> None:
    from openprogram.models.gateway.agents import find
    monkeypatch.setenv("PATH", str(qoder / "nobin"))
    path = qoder / ".qoder" / "settings.json"
    path.parent.mkdir()
    path.write_text('{"enabledPlugins": {}}')
    agent = find("qoder")
    assert not agent.installed()
    (qoder / ".qoder" / ".qoder-app-status.json").write_text("{}")
    assert agent.installed() and not agent.cli_gone()  # the app runs it (magpie sign)
