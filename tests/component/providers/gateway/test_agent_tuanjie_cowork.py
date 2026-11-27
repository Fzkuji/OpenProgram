"""Tuanjie Cowork, after yetone/magpie ``internal/agent/tuanjiecowork_test.go`` (MIT License)."""
from __future__ import annotations

import json
import os
import sys

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

V1 = "http://127.0.0.1:18100/gateway/openai/v1"

USER = """{
  "migratedToSingleProvider": true,
  "migratedToMultiProvider": true,
  "customModelProviders": [
    {
      "id": "custom-provider",
      "name": "My Relay",
      "baseUrl": "https://relay.example/v1",
      "apiKey": "sk-own",
      "authType": "openai",
      "flashModelId": "glm-4.5"
    }
  ],
  "customModels": [
    {
      "id": "glm-4.5",
      "model": "glm-4.5",
      "providerId": "custom-provider",
      "displayName": "glm-4.5",
      "roles": ["model", "flashModel"],
      "wireApi": "chat",
      "supportsMultimodal": false
    }
  ],
  "language": "zh-CN"
}
"""


@pytest.fixture
def cowork(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import tuanjie_cowork
    monkeypatch.setattr(agents, "all_agents", lambda: [tuanjie_cowork.agent()])
    return agent_home / ".codely" / "settings.json"


def test_cowork_adds_its_provider_and_model(cowork) -> None:
    cowork.parent.mkdir()
    cowork.write_text(USER)
    engine.connect("tuanjie-cowork", {"model": "openai/gpt-5"})
    data = json.loads(cowork.read_text())
    providers, models = data["customModelProviders"], data["customModels"]
    assert providers[0]["id"] == "custom-provider" and models[0]["id"] == "glm-4.5"
    assert providers[1] == {"apiKey": token_for("tuanjie-cowork"), "authType": "openai", "baseUrl": V1,
                            "id": "openprogram", "name": "openprogram"}
    assert models[1] == {"displayName": "openai/gpt-5", "id": "openprogram", "model": "openai/gpt-5",
                         "providerId": "openprogram", "roles": ["model"], "supportsMultimodal": True, "wireApi": "chat"}
    assert data["language"] == "zh-CN"

    # a name and roles given them in Cowork stay through another pick
    data["customModelProviders"][1]["name"] = "Gateway"
    data["customModels"][1]["roles"] = ["model", "flashModel"]
    cowork.write_text(json.dumps(data, indent=2))
    engine.connect("tuanjie-cowork", {"model": "deepseek/deepseek-chat"})
    data = json.loads(cowork.read_text())
    assert data["customModelProviders"][1]["name"] == "Gateway"
    assert data["customModels"][1]["roles"] == ["model", "flashModel"]
    assert data["customModels"][1]["model"] == "deepseek/deepseek-chat"
    assert data["customModels"][1]["supportsMultimodal"] is False

    engine.disconnect("tuanjie-cowork")
    back = json.loads(cowork.read_text())
    assert back == json.loads(USER)


def test_cowork_made_file_goes_again(cowork) -> None:
    engine.connect("tuanjie-cowork", {"model": "openai/gpt-5"})
    if sys.platform != "win32":
        assert os.stat(cowork).st_mode & 0o777 == 0o600  # it holds keys, as Cowork's own does
    engine.disconnect("tuanjie-cowork")
    assert not cowork.exists()


@pytest.mark.parametrize("file", [
    '{"customModelProviders":[{"id":"openprogram","name":"m","baseUrl":"https://relay.example/v1",'
    '"apiKey":"sk-own","authType":"openai"}],"customModels":[]}',
    '{"customModelProviders":[{"id":"custom-provider","name":"m","baseUrl":"https://relay.example/v1",'
    '"apiKey":"sk-own","authType":"openai"}],"customModels":[{"id":"openprogram","model":"x",'
    '"providerId":"custom-provider","roles":["model"],"wireApi":"chat"}]}',
])
def test_cowork_leaves_the_users_own_openprogram(cowork, file) -> None:
    cowork.parent.mkdir()
    cowork.write_text(file)
    with pytest.raises(engine.AgentConfigError, match="of its own"):
        engine.connect("tuanjie-cowork", {"model": "openai/gpt-5"})
    assert cowork.read_text() == file
