"""QwenPaw, after yetone/magpie ``internal/agent/qwenpaw_test.go`` (MIT License)."""
from __future__ import annotations

import json
import os
import sys

import pytest

from openprogram.models.gateway.agents import engine
from openprogram.models.gateway.keys import token_for

V1 = "http://127.0.0.1:18100/gateway/openai/v1"


def _agent_json(agent_id: str, name: str, backend: str) -> str:
    return json.dumps({"id": agent_id, "name": name, "description": "", "workspace_dir": "", "backend": backend,
                       "active_model": {"provider_id": "deepseek", "model": "deepseek-chat"},
                       "fallback_models": [{"provider_id": "deepseek", "model": "deepseek-reasoner"}],
                       "language": "zh", "running": {"max_iters": 100}}, indent=2) + "\n"


@pytest.fixture
def qwenpaw(agent_home, monkeypatch):
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import qwenpaw as module
    monkeypatch.delenv("QWENPAW_WORKING_DIR", raising=False)
    monkeypatch.delenv("QWENPAW_SECRET_DIR", raising=False)
    work = agent_home / ".qwenpaw"
    profiles = {i: {"id": i, "workspace_dir": str(work / "workspaces" / i), "enabled": i != "old", "pinned": False}
                for i in ("default", "writer", "coder", "old")}
    (work).mkdir()
    (work / "config.json").write_text(json.dumps({"agents": {"active_agent": "default",
                                                             "agent_order": list(profiles), "profiles": profiles}}))
    for agent_id, name, backend in (("default", "Default Agent", "qwenpaw"), ("writer", "Writer", "qwenpaw"),
                                    ("coder", "Coder", "claude_code"), ("old", "Old", "qwenpaw")):
        folder = work / "workspaces" / agent_id
        folder.mkdir(parents=True)
        (folder / "agent.json").write_text(_agent_json(agent_id, name, backend))
    monkeypatch.setattr(agents, "all_agents", lambda: [module.agent(), *module.extra_agents()])
    return work


def test_qwenpaw_connect_and_disconnect(qwenpaw) -> None:
    path = qwenpaw / "workspaces" / "default" / "agent.json"
    prov = qwenpaw.parent / ".qwenpaw.secret" / "providers" / "custom" / "openprogram.json"
    before = path.read_text()
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    data = json.loads(path.read_text())
    assert data["active_model"] == {"provider_id": "openprogram", "model": "deepseek/deepseek-chat"}
    assert data["fallback_models"][0]["model"] == "deepseek-reasoner" and data["running"] == {"max_iters": 100}
    provider = json.loads(prov.read_text())
    assert provider["id"] == "openprogram" and provider["base_url"] == V1
    assert provider["api_key"] == token_for("qwenpaw") and provider["chat_model"] == "OpenAIChatModel"
    assert provider["is_custom"] is True and provider["enabled"] is True
    first, second = provider["extra_models"]
    assert first == {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "source": "user", "supports_image": False,
                     "supports_multimodal": False, "supports_tool_calling": True, "max_input_length": 128000,
                     "max_input_length_configured": True, "max_output_length": 8192,
                     "thinking_param_style": "effort", "reasoning_effort_options": ["low", "medium", "high"]}
    assert second["supports_image"] is True and "max_input_length" not in second
    assert "thinking_param_style" not in second
    if sys.platform != "win32":
        assert os.stat(prov).st_mode & 0o077 == 0
    engine.disconnect("qwenpaw")
    assert path.read_text() == before
    assert not prov.exists()


def test_qwenpaw_agents_are_rows(qwenpaw) -> None:
    from openprogram.models.gateway.agents import all_agents
    prov = qwenpaw.parent / ".qwenpaw.secret" / "providers" / "custom" / "openprogram.json"
    rows = [(a.id, a.name) for a in all_agents()]
    assert rows == [("qwenpaw", "QwenPaw"), ("qwenpaw#writer", "QwenPaw · Writer")]
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    engine.connect("qwenpaw#writer", {"model": "openai/gpt-5"})
    writer = qwenpaw / "workspaces" / "writer" / "agent.json"
    assert json.loads(writer.read_text())["active_model"]["model"] == "openai/gpt-5"
    engine.disconnect("qwenpaw#writer")
    assert json.loads(writer.read_text())["active_model"] == {"provider_id": "deepseek", "model": "deepseek-chat"}
    assert prov.exists(), "the writer's disconnect took the provider the default agent is on"
    engine.disconnect("qwenpaw")
    assert not prov.exists()


def test_qwenpaw_provider_goes_with_the_last_agent_whose_stash_names_it(qwenpaw) -> None:
    """The shared provider file (Write.shared): the first agent to connect
    leaves first, and the file stays until the last one's stash is gone."""
    from openprogram.models.gateway import stash
    prov = qwenpaw.parent / ".qwenpaw.secret" / "providers" / "custom" / "openprogram.json"
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    engine.connect("qwenpaw#writer", {"model": "openai/gpt-5"})
    record = stash.entry("qwenpaw#writer")["files"][str(prov.resolve())]
    assert record["shared"] and record["absent"] is True  # what was there before the first agent
    data = json.loads(prov.read_text())
    data["note"] = "added in QwenPaw's console"
    prov.write_text(json.dumps(data))
    engine.disconnect("qwenpaw")
    assert prov.exists()
    engine.disconnect("qwenpaw#writer")
    assert not prov.exists()  # OpenProgram's whole, edited or not


def test_qwenpaw_sync_keeps_what_qwenpaw_changed(qwenpaw) -> None:
    prov = qwenpaw.parent / ".qwenpaw.secret" / "providers" / "custom" / "openprogram.json"
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    provider = json.loads(prov.read_text())
    provider["api_key"] = "ENC:gAAAAABmagpie"
    provider["custom_headers"] = {"X-Team": "a"}
    provider["extra_models"][0]["max_input_length"] = 64000
    provider["extra_models"][0]["config_overrides"] = ["max_input_length"]
    prov.write_text(json.dumps(provider, indent=2))
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    again = json.loads(prov.read_text())
    assert again["api_key"] == "ENC:gAAAAABmagpie" and again["custom_headers"] == {"X-Team": "a"}
    assert again["extra_models"][0]["max_input_length"] == 64000


def test_qwenpaw_stays_owned_after_qwenpaw_encrypts_the_key(qwenpaw, models, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import lifecycle

    from .conftest import MODELS
    prov = qwenpaw.parent / ".qwenpaw.secret" / "providers" / "custom" / "openprogram.json"
    engine.connect("qwenpaw", {"model": "deepseek/deepseek-chat"})
    provider = json.loads(prov.read_text())
    provider["api_key"] = "ENC:gAAAAABmagpie"            # QwenPaw at its next start
    provider["name"] = "Gateway"                          # the console
    provider["custom_headers"] = {"X-Team": "a"}
    provider["extra_models"][0]["generate_kwargs"] = {"temperature": 0.2}
    prov.write_text(json.dumps(provider, indent=2))
    assert engine.owned("qwenpaw") and engine.status("qwenpaw")["config"]["drift"] == []

    models([*MODELS, {"id": "later/m1", "name": "m1"}])
    assert lifecycle.resync() == ["qwenpaw"]
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["QwenPaw"]
    again = json.loads(prov.read_text())
    assert again["base_url"].startswith("http://127.0.0.1:18200/") and again["api_key"] == "ENC:gAAAAABmagpie"
    assert again["extra_models"][-1]["id"] == "later/m1" and again["custom_headers"] == {"X-Team": "a"}
    engine.disconnect("qwenpaw")
    assert not prov.exists()


def test_qwenpaw_refuses_acp_and_unstarted(qwenpaw) -> None:
    from openprogram.models.gateway.agents import Agent
    from openprogram.models.gateway.agents import qwenpaw as module
    coder = qwenpaw / "workspaces" / "coder"
    before = (coder / "agent.json").read_text()
    with pytest.raises(engine.AgentConfigError, match="claude_code"):
        module._wire_for(coder)(engine.wiring(Agent(id="x", name="x", icon="", path=lambda: coder,
                                                    wire=lambda at: []), {"model": "openai/gpt-5"}))
    assert (coder / "agent.json").read_text() == before
    (qwenpaw / "workspaces" / "default" / "agent.json").unlink()
    with pytest.raises(engine.AgentConfigError, match="start QwenPaw once"):
        engine.connect("qwenpaw", {"model": "openai/gpt-5"})


def test_qwenpaw_folders(agent_home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import qwenpaw as module
    monkeypatch.delenv("QWENPAW_WORKING_DIR", raising=False)
    monkeypatch.delenv("QWENPAW_SECRET_DIR", raising=False)
    assert module.working() == agent_home / ".qwenpaw"
    (agent_home / ".copaw").mkdir()
    assert module.working() == (agent_home / ".copaw").resolve()
    monkeypatch.setenv("QWENPAW_WORKING_DIR", "~/pw")
    work = module.working()
    assert work == agent_home / "pw"
    assert module.secret(work) == agent_home / "pw.secret"
    work.mkdir()
    (work / ".env").write_text("export QWENPAW_SECRET_DIR='~/keys' # mine\n")
    assert module.secret(work) == agent_home / "keys"
