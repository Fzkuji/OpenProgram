"""Paimon, after yetone/magpie ``internal/agent/paimon_test.go`` (MIT License)."""
from __future__ import annotations

import json
import stat

import pytest

from openprogram.models.gateway.agents import engine

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


@pytest.fixture
def paimon(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import paimon
    monkeypatch.setattr(agents, "all_agents", lambda: [paimon.agent()])
    monkeypatch.delenv("PAIMON_CONFIG_HOME", raising=False)
    return agent_home / ".config" / "paimon" / "config.json"


def test_paimon_litellm_and_restore(paimon) -> None:
    from openprogram.models.gateway.keys import token_for
    paimon.parent.mkdir(parents=True)
    user = {"model": "openai:gpt-4o", "providers": {"openai": {"api_key": "sk"}},
            "compaction": {"context_window": 64000}}
    paimon.write_text(json.dumps(user, indent=2) + "\n")
    engine.connect("paimon", {"model": "deepseek/deepseek-chat"})
    data = json.loads(paimon.read_text())
    assert data["model"] == "litellm:deepseek/deepseek-chat"
    assert data["providers"] == {"openai": {"api_key": "sk"},
                                 "litellm": {"api_base": V1, "api_key": token_for("paimon")}}
    assert data["compaction"] == {"context_window": 128000}
    # a model whose window isn't known leaves the window alone
    engine.connect("paimon", {"model": "openai/gpt-5"})
    assert json.loads(paimon.read_text())["compaction"] == {"context_window": 128000}
    engine.disconnect("paimon")
    assert json.loads(paimon.read_text()) == user


def test_paimon_new_file_is_private_and_goes(paimon) -> None:
    engine.connect("paimon", {"model": "openai/gpt-5"})
    assert stat.S_IMODE(paimon.stat().st_mode) == 0o600
    engine.disconnect("paimon")
    assert not paimon.exists()


def test_paimon_config_home(paimon, monkeypatch, agent_home) -> None:
    from openprogram.models.gateway.agents import paimon as module
    monkeypatch.setenv("PAIMON_CONFIG_HOME", "relative")
    assert module.config_path() == paimon
    monkeypatch.setenv("PAIMON_CONFIG_HOME", str(agent_home / "p"))
    assert module.config_path() == agent_home / "p" / "config.json"
