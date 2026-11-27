"""Qwen Code, and lists shared with the user's own entries (``Members``),
after yetone/magpie ``internal/agent/qwen_test.go`` (MIT License)."""
from __future__ import annotations

import json

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

from .conftest import MODELS

V1 = "http://127.0.0.1:18100/gateway/openai/v1"

USER = """{
  "env": {
    "IDEALAB_API_KEY": "team-key"
  },
  "modelProviders": {
    "openai": [
      {
        "baseUrl": "https://idealab.example/v1",
        "envKey": "IDEALAB_API_KEY",
        "id": "glm-5",
        "name": "[IdeaLab] glm-5"
      }
    ]
  },
  "model": {
    "name": "glm-5"
  },
  "outputLanguage": "zh-CN"
}
"""


def test_qwen_connect_sync_and_restore(agent_home, models) -> None:
    from openprogram.models.gateway.agents import find
    path = agent_home / ".qwen" / "settings.json"
    path.parent.mkdir()
    path.write_text(USER)
    agent = find("qwen")
    assert agent.path() == path and agent.installed() and find("qwen-code").id == "qwen"

    engine.connect("qwen", {"model": "deepseek/deepseek-chat"})
    data = json.loads(path.read_text())
    listed = data["modelProviders"]["openai"]
    assert listed[0]["id"] == "glm-5"  # the user's entry stays first, as it was
    assert listed[1] == {"baseUrl": V1, "envKey": "OPENPROGRAM_QWEN_API_KEY",
                         "generationConfig": {"contextWindowSize": 128000},
                         "id": "deepseek/deepseek-chat", "name": "DeepSeek Chat"}
    assert listed[2] == {"baseUrl": V1, "envKey": "OPENPROGRAM_QWEN_API_KEY", "id": "openai/gpt-5", "name": "GPT-5"}
    assert data["model"] == {"name": "deepseek/deepseek-chat", "baseUrl": V1}
    assert data["env"] == {"IDEALAB_API_KEY": "team-key", "OPENPROGRAM_QWEN_API_KEY": token_for("qwen")}
    assert data["security"] == {"auth": {"selectedType": "openai"}} and data["outputLanguage"] == "zh-CN"
    assert engine.status("qwen")["config"]["drift"] == []

    # a model enabled since joins the list on sync; the model stays
    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    assert engine.sync("qwen")
    assert [e["id"] for e in json.loads(path.read_text())["modelProviders"]["openai"]][-1] == "later/m1"

    # an entry the user added meanwhile is theirs and stays after disconnect
    data = json.loads(path.read_text())
    data["modelProviders"]["openai"].append({"id": "mine", "envKey": "X", "baseUrl": "http://x"})
    path.write_text(json.dumps(data, indent=2))
    assert engine.status("qwen")["config"]["drift"] == []
    engine.disconnect("qwen")
    back = json.loads(path.read_text())
    assert [e["id"] for e in back["modelProviders"]["openai"]] == ["glm-5", "mine"]
    assert back["model"] == {"name": "glm-5"} and back["env"] == {"IDEALAB_API_KEY": "team-key"}
    assert "security" not in back


def test_qwen_restores_byte_for_byte(agent_home) -> None:
    path = agent_home / ".qwen" / "settings.json"
    path.parent.mkdir()
    path.write_text(USER)
    engine.connect("qwen", {"model": "openai/gpt-5"})
    engine.disconnect("qwen")
    assert path.read_text() == USER


def test_qwen_fresh_file_goes_again(agent_home, monkeypatch) -> None:
    monkeypatch.setenv("QWEN_HOME", str(agent_home / "elsewhere"))
    path = agent_home / "elsewhere" / "settings.json"
    engine.connect("qwen", {"model": "openai/gpt-5"})
    assert json.loads(path.read_text())["model"]["name"] == "openai/gpt-5"
    engine.disconnect("qwen")
    assert not path.exists()


def test_a_list_that_is_the_whole_file(agent_home, monkeypatch) -> None:
    """A JSON file that is one list (WorkBuddy's models.json) is shared the same way."""
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import ROOT, Agent, Members, Write
    path = agent_home / "models.json"
    path.write_text('[{"id": "mine"}]\n')

    def wire(at):
        return [Write(path, "json", members={ROOT: Members({"vendor": "openprogram"},
                                                           [{"id": at.model, "vendor": "openprogram"}])})]
    made = Agent(id="listed", name="Listed", icon="", path=lambda: path, wire=wire)
    monkeypatch.setattr(agents, "all_agents", lambda: [made])
    engine.connect("listed", {"model": "openai/gpt-5"})
    assert json.loads(path.read_text()) == [{"id": "mine"}, {"id": "openai/gpt-5", "vendor": "openprogram"}]
    assert engine.status("listed")["config"]["drift"] == []
    path.write_text('[{"id": "mine"}]\n')
    assert engine.status("listed")["config"]["drift"] == ["$"]
    engine.connect("listed", {"model": "openai/gpt-5"})
    engine.disconnect("listed")
    assert json.loads(path.read_text()) == [{"id": "mine"}]


def test_a_byte_order_mark_is_kept(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import Agent, Write
    path = agent_home / "cmd_config.json"
    path.write_bytes(b'\xef\xbb\xbf{"a": 1}\n')
    made = Agent(id="bom", name="Bom", icon="", path=lambda: path,
                 wire=lambda at: [Write(path, "json", sets={"model": at.model})])
    monkeypatch.setattr(agents, "all_agents", lambda: [made])
    engine.connect("bom", {"model": "openai/gpt-5"})
    assert path.read_bytes().startswith(b'\xef\xbb\xbf{')
    engine.disconnect("bom")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf") and json.loads(path.read_text(encoding="utf-8-sig")) == {"a": 1}


def test_an_owned_entry_goes_whole_and_loose_fields_stay_owned(agent_home, monkeypatch) -> None:
    """An object OpenProgram owns (magpie DelJSON of its own entry): the user's
    additions inside it stay while connected and go with it on disconnect; a
    loose field the agent rewrites is no drift and keeps sync and rewire on."""
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import Agent, Members, Write
    path = agent_home / "settings.json"
    path.write_text('{"providers": {"mine": {"url": "x"}}, "list": [{"id": "a"}, {"id": "op"}, {"id": "b"}]}\n')

    def wire(at):
        entry = {"url": at.base("openai-chat"), "key": at.key, "model": at.model}
        return [Write(path, "json", sets={"providers.openprogram": entry, "model": at.model},
                      owns=("providers.openprogram",), loose=("providers.openprogram.key",),
                      members={"list": Members({"id": {"in": ["op", "op2"]}}, [{"id": "op", "n": 1}, {"id": "op2"}],
                                               keep_place=True, loose=("n",))})]
    made = Agent(id="owner", name="Owner", icon="", path=lambda: path, wire=wire)
    monkeypatch.setattr(agents, "all_agents", lambda: [made])
    engine.connect("owner", {"model": "openai/gpt-5"})
    data = json.loads(path.read_text())
    assert [e["id"] for e in data["list"]] == ["a", "op", "op2", "b"]  # where OpenProgram's was
    data["providers"]["openprogram"]["headers"] = {"x": "y"}   # added by the user: kept, not drift
    data["providers"]["openprogram"]["key"] = "ENC:secret"     # rewritten by the agent: loose
    data["list"][1]["n"] = 5
    path.write_text(json.dumps(data))
    assert engine.status("owner")["config"]["drift"] == [] and engine.owned("owner")
    engine.connect("owner", {"model": "deepseek/deepseek-chat"})
    assert json.loads(path.read_text())["providers"]["openprogram"]["headers"] == {"x": "y"}
    data = json.loads(path.read_text())
    data["providers"]["openprogram"]["url"] = "http://elsewhere"
    path.write_text(json.dumps(data))
    assert engine.status("owner")["config"]["drift"] == ["providers.openprogram"]
    engine.disconnect("owner")
    assert json.loads(path.read_text()) == {"providers": {"mine": {"url": "x"}}, "list": [{"id": "a"}, {"id": "b"}]}


def test_detect_sign_and_bin_not(tmp_path, monkeypatch) -> None:
    from openprogram.models.gateway.agents import Agent
    binary = tmp_path / "bin" / "tool"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent))
    folder = tmp_path / ".tool"
    folder.mkdir()
    (folder / "settings.json").write_text("{}")
    base = dict(id="tool", name="Tool", icon="", path=lambda: folder / "settings.json", wire=lambda at: [],
                bin="tool", dir=lambda: folder)
    assert Agent(**base).bin_path() and not Agent(**base, bin_not=lambda found: True).bin_path()
    assert Agent(**base, bin_not=lambda found: True).cli_gone()
    assert not Agent(**base, bin_not=lambda found: True, sign=lambda: True).cli_gone()
    assert not Agent(**base, detect=lambda: False, detect_only=True).installed()


def test_a_shared_file_keeps_the_original_of_the_first_agent(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import Agent, Write
    path = agent_home / "shared.json"
    path.write_text('{"mine": 1, "url": "theirs"}\n')

    def make(agent_id):
        return Agent(id=agent_id, name=agent_id, icon="", path=lambda: path,
                     wire=lambda at: [Write(path, "json", sets={"url": at.base("openai-chat")}, shared=True)])
    made = [make("one"), make("two")]
    monkeypatch.setattr(agents, "all_agents", lambda: made)
    engine.connect("one", {"model": "openai/gpt-5"})
    engine.connect("two", {"model": "openai/gpt-5"})
    engine.disconnect("one")
    assert json.loads(path.read_text())["url"].endswith("/openai/v1")
    engine.disconnect("two")
    assert json.loads(path.read_text()) == {"mine": 1, "url": "theirs"}
