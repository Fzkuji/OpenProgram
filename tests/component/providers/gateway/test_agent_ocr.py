"""Open Code Review, after yetone/magpie ``internal/agent/ocr_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for


@pytest.fixture
def ocr(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import ocr as module
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent()])
    return agent_home / ".opencodereview" / "config.json"


def test_ocr_wiring(ocr) -> None:
    from openprogram.models.gateway.agents import agent_of_ua
    ocr.parent.mkdir()
    user = {"provider": "deepseek", "model": "deepseek-chat", "providers": {"deepseek": {"api_key": "sk"}}}
    ocr.write_text(json.dumps(user, indent=2) + "\n")
    engine.connect("open-code-review", {"model": "openai/gpt-5"})
    data = json.loads(ocr.read_text())
    assert data["provider"] == "openprogram" and data["model"] == "deepseek-chat"
    assert data["custom_providers"]["openprogram"] == {
        "protocol": "openai", "url": "http://127.0.0.1:18100/gateway/openai/v1",
        "api_key": token_for("open-code-review"), "model": "openai/gpt-5"}
    assert agent_of_ua("open-code-review/0.3.1") == "open-code-review"
    engine.disconnect("open-code-review")
    assert json.loads(ocr.read_text()) == user


def test_ocr_not_ours(tmp_path) -> None:
    from openprogram.models.gateway.agents.ocr import ours
    assert ours("/usr/local/lib/node_modules/@alibaba-group/open-code-review/bin/ocr")
    plain = tmp_path / "ocr"
    plain.write_text("")
    assert not ours(str(plain))
