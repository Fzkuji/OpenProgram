"""WorkBuddy and WorkBuddy AI, after yetone/magpie ``internal/agent/workbuddy_test.go`` (MIT License)."""
from __future__ import annotations

import json

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

V1 = "http://127.0.0.1:18100/gateway/openai/v1"
LOCAL = '[{"id":"qwen-local","local":true,"url":"http://127.0.0.1:8080/v1/chat/completions"}]'


@pytest.fixture
def buddy(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import workbuddy
    monkeypatch.delenv("WORKBUDDY_CONFIG_DIR", raising=False)
    monkeypatch.setattr(agents, "all_agents", lambda: [workbuddy.agent(), *workbuddy.extra_agents()])
    return agent_home


def test_workbuddy_bare_list(buddy, monkeypatch) -> None:
    from openprogram.models.gateway.agents import find
    path = buddy / ".workbuddy" / "models.json"
    path.parent.mkdir()
    path.write_text(LOCAL)  # WorkBuddy's own local model, which stays
    monkeypatch.setattr("sys.platform", "linux")  # found by its folder, as off a Mac
    assert find("workbuddy").installed()

    engine.connect("workbuddy", {"model": "deepseek/deepseek-chat"})
    models = json.loads(path.read_text())
    assert models[0] == json.loads(LOCAL)[0] and len(models) == 3
    chat = models[1]
    assert (chat["id"], chat["vendor"], chat["apiKey"], chat["url"]) == (
        "deepseek/deepseek-chat", "openprogram", token_for("workbuddy"), V1 + "/chat/completions")
    assert chat["supportsToolCall"] is True and chat["maxInputTokens"] == 128000 and chat["maxOutputTokens"] == 8192
    assert chat["reasoning"] == {"supportedEfforts": ["low", "medium", "high"], "canDisableThinking": False,
                                 "defaultEffort": "medium"}

    # turned off in WorkBuddy, with a default effort of its own: both stay through a sync
    models[1]["disabled"] = True
    models[1]["reasoning"]["defaultEffort"] = "high"
    path.write_text(json.dumps(models))
    engine.sync("workbuddy")
    models = json.loads(path.read_text())
    assert models[1]["disabled"] is True and models[1]["reasoning"]["defaultEffort"] == "high"

    engine.disconnect("workbuddy")
    assert json.loads(path.read_text()) == json.loads(LOCAL)


def test_workbuddy_object_form_keeps_showing_its_models(buddy) -> None:
    path = buddy / ".workbuddy" / "models.json"
    path.parent.mkdir()
    path.write_text('{"models":[{"id":"mine","url":"https://x/v1/chat/completions"}],"availableModels":["mine"],"other":1}')
    engine.connect("workbuddy", {"model": "openai/gpt-5"})
    doc = json.loads(path.read_text())
    assert [m["id"] for m in doc["models"]] == ["mine", "deepseek/deepseek-chat", "openai/gpt-5"]
    assert doc["availableModels"] == ["mine", "deepseek/deepseek-chat", "openai/gpt-5"] and doc["other"] == 1
    # a model the user adds after OpenProgram's stays after them on a sync (magpie buddyWriteAt)
    doc["models"].append({"id": "late", "url": "https://y/v1/chat/completions"})
    path.write_text(json.dumps(doc))
    engine.sync("workbuddy")
    assert [m["id"] for m in json.loads(path.read_text())["models"]] == [
        "mine", "deepseek/deepseek-chat", "openai/gpt-5", "late"]
    doc = json.loads(path.read_text())
    doc["models"].pop()
    path.write_text(json.dumps(doc))
    engine.disconnect("workbuddy")
    doc = json.loads(path.read_text())
    assert doc == {"models": [{"id": "mine", "url": "https://x/v1/chat/completions"}], "availableModels": ["mine"],
                   "other": 1}


def test_workbuddy_new_file_is_a_bare_list(buddy) -> None:
    path = buddy / ".workbuddy" / "models.json"
    engine.connect("workbuddy", {"model": "openai/gpt-5"})
    assert isinstance(json.loads(path.read_text()), list)
    engine.disconnect("workbuddy")
    assert not path.exists()


def test_workbuddy_ai_writes_its_own_folder(buddy) -> None:
    china = buddy / ".workbuddy" / "models.json"
    china.parent.mkdir()
    china.write_text(LOCAL)
    engine.connect("workbuddy-ai", {"model": "openai/gpt-5"})
    models = json.loads((buddy / ".workbuddy-ai" / "models.json").read_text())
    assert all(m["apiKey"] == token_for("workbuddy-ai") for m in models)
    assert china.read_text() == LOCAL


def test_buddy_model_efforts_and_windows() -> None:
    from openprogram.models.gateway.agents.workbuddy import buddy_model
    entry = buddy_model({"id": "x/y", "name": "Y", "max_tokens": 500000, "vision": True,
                         "thinking_levels": ["none", "low", "high"]}, "k", V1)
    assert entry["maxInputTokens"] == 200000 and entry["maxOutputTokens"] == 128000
    assert entry["reasoning"] == {"supportedEfforts": ["low", "high"], "canDisableThinking": True, "defaultEffort": "high"}
    plain = buddy_model({"id": "x/z", "name": "Z", "context_window": 1000}, "k", V1)
    assert "reasoning" not in plain and plain["supportsReasoning"] is False and "maxOutputTokens" not in plain
    # more than 200K: WorkBuddy's context menu, set to the whole window
    big = buddy_model({"id": "x/o", "context_window": 1000000}, "k", V1)
    assert big["contextWindow"] == {"supportedLengths": [200000, 1000000], "defaultLength": 1000000}
