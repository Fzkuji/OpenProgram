"""Snow CLI, after yetone/magpie ``internal/agent/snow_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

V1 = "http://127.0.0.1:18100/gateway/openai/v1"

DEFAULT = """{
  "snowcfg": {
    "baseUrl": "https://idealab.example.com/v1",
    "baseUrlMode": "auto",
    "apiKey": "team-key",
    "requestMethod": "chat",
    "advancedModel": "glm-5",
    "basicModel": "glm-5-air",
    "supportsVision": true,
    "maxContextTokens": 200000,
    "maxTokens": 64000,
    "streamingDisplay": false,
    "systemPromptId": "terse",
    "visionModel": "qwen-vl"
  },
  "companionMuted": true
}"""

WORK = """{
  "snowcfg": {
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "",
    "requestMethod": "responses",
    "advancedModel": "kimi-k3",
    "oauth": {"provider": "example", "accessToken": "secret"}
  }
}"""

ACTIVE = '{\n  "activeProfile": "default"\n}'


@pytest.fixture
def snow(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import snow as module
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    folder = agent_home / ".snow"
    (folder / "profiles").mkdir(parents=True)
    return folder


def _user(folder) -> None:
    (folder / "profiles" / "default.json").write_text(DEFAULT)
    (folder / "profiles" / "work.json").write_text(WORK)
    (folder / "active-profile.json").write_text(ACTIVE)
    (folder / "config.json").write_text(DEFAULT)


def _cfg(path):
    return json.loads(path.read_text())["snowcfg"]


def test_snow_profile_connect_and_back(snow, models, monkeypatch) -> None:
    _user(snow)
    ours = snow / "profiles" / "openprogram.json"
    engine.connect("snow", {"model": "deepseek/deepseek-chat"})
    cfg = _cfg(ours)
    assert {k: cfg[k] for k in ("baseUrl", "baseUrlMode", "apiKey", "requestMethod", "advancedModel", "basicModel")} == {
        "baseUrl": V1, "baseUrlMode": "base", "apiKey": token_for("snow"), "requestMethod": "chat",
        "advancedModel": "deepseek/deepseek-chat", "basicModel": "deepseek/deepseek-chat"}
    # the user's own settings come along; a vision model on the profile's own endpoint does not
    assert cfg["streamingDisplay"] is False and cfg["systemPromptId"] == "terse" and "visionModel" not in cfg
    assert (cfg["maxContextTokens"], cfg["maxTokens"], cfg["supportsVision"]) == (128000, 8192, False)
    assert json.loads(ours.read_text())["companionMuted"] is True
    assert json.loads((snow / "active-profile.json").read_text()) == {"activeProfile": "openprogram"}
    assert (snow / "config.json").read_text() == ours.read_text()
    assert (snow / "profiles" / "default.json").read_text() == DEFAULT
    assert (snow / "profiles" / "work.json").read_text() == WORK
    assert engine.status("snow")["config"]["drift"] == []

    # another model, and a light model of its own: the same profile, moved
    engine.connect("snow", {"model": "openai/gpt-5", "small": "deepseek/deepseek-chat"})
    cfg = _cfg(ours)
    assert (cfg["advancedModel"], cfg["basicModel"], cfg["streamingDisplay"]) == ("openai/gpt-5", "deepseek/deepseek-chat", False)
    assert (cfg["maxContextTokens"], cfg["maxTokens"], cfg["supportsVision"]) == (200000, 64000, True)

    # a sync rewrites the profile and its copy
    models([*MODELS[:1], {**MODELS[1], "context_window": 400000}])
    assert engine.sync("snow")
    assert _cfg(ours)["maxContextTokens"] == 400000 and (snow / "config.json").read_text() == ours.read_text()

    # a setting the user adds in OpenProgram's profile stays across a rewire
    data = json.loads(ours.read_text())
    data["snowcfg"]["mine"] = 1
    ours.write_text(json.dumps(data))
    assert engine.owned("snow")
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["Snow CLI"]
    assert _cfg(ours)["baseUrl"].startswith("http://127.0.0.1:18200/") and _cfg(ours)["mine"] == 1

    # edited since, OpenProgram's profile still goes on disconnect (magpie removes magpie.json)
    data = json.loads(ours.read_text())
    data["snowcfg"]["mine"] = 2
    ours.write_text(json.dumps(data))
    engine.disconnect("snow")
    assert (snow / "active-profile.json").read_text() == ACTIVE
    assert not ours.exists() and (snow / "config.json").read_text() == DEFAULT


def test_snow_never_carries_a_sign_in(snow) -> None:
    _user(snow)
    (snow / "active-profile.json").write_text('{"activeProfile": "work"}')
    engine.connect("snow", {"model": "openai/gpt-5"})
    cfg = _cfg(snow / "profiles" / "openprogram.json")
    assert "oauth" not in cfg and cfg["requestMethod"] == "chat" and cfg["apiKey"] == token_for("snow")
    engine.disconnect("snow")
    assert json.loads((snow / "active-profile.json").read_text()) == {"activeProfile": "work"}


def test_snow_users_own_openprogram_profile_comes_back(snow) -> None:
    _user(snow)
    theirs = '{"snowcfg": {"apiKey": "mine", "advancedModel": "x"}}'
    (snow / "profiles" / "openprogram.json").write_text(theirs)
    engine.connect("snow", {"model": "openai/gpt-5"})
    assert _cfg(snow / "profiles" / "openprogram.json")["advancedModel"] == "openai/gpt-5"
    engine.disconnect("snow")
    assert (snow / "profiles" / "openprogram.json").read_text() == theirs


def test_snow_from_before_profiles(snow) -> None:
    """config.json alone: the user's is copied to a default profile first, and
    the active-profile file Snow didn't have goes again."""
    (snow / "config.json").write_text(DEFAULT)
    engine.connect("snow", {"model": "openai/gpt-5"})
    assert (snow / "profiles" / "default.json").read_text() == DEFAULT
    assert _cfg(snow / "profiles" / "openprogram.json")["systemPromptId"] == "terse"
    engine.disconnect("snow")
    assert not (snow / "active-profile.json").exists()
    assert (snow / "config.json").read_text() == DEFAULT


def test_snow_config_dir(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import snow as module
    monkeypatch.setenv("SNOW_CONFIG_DIR", str(agent_home / "elsewhere"))
    assert module.profile_path() == agent_home / "elsewhere" / "profiles" / "openprogram.json"
    monkeypatch.setenv("SNOW_CONFIG_DIR", "relative")
    assert module.home() == agent_home / ".snow"
