"""Kiro Crew runs on a harness connected to OpenProgram, after yetone/magpie's
``internal/agent/kirocrew_test.go`` (MIT)."""
from __future__ import annotations

import json
import os
import sys

import pytest

from openprogram.models.gateway.agents import kiro_crew


@pytest.fixture
def crew(tmp_path, monkeypatch):
    import openprogram.models.gateway.agents as registry
    if "kiro_crew" not in registry.MODULES:
        monkeypatch.setattr(registry, "MODULES", (*registry.MODULES, "kiro_crew"))
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "crew"))
    monkeypatch.setattr("openprogram.models.gateway.address.worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr("openprogram.models.gateway.conversion.enabled_model_refs",
                        lambda: [{"id": "deepseek/deepseek-chat", "name": "DS", "provider": "deepseek"}])
    (tmp_path / "crew").mkdir()
    on: set[str] = set()
    joined: list[tuple[str, str]] = []
    left: list[str] = []
    monkeypatch.setattr(kiro_crew, "wired", lambda agent_id: agent_id in on)
    monkeypatch.setattr(kiro_crew, "has_adapter", lambda h: True)

    def join(agent_id, model):
        if agent_id not in ("codex", "claude_code"):
            return False
        joined.append((agent_id, model))
        on.add(agent_id)
        return True

    def leave(agent_id):
        left.append(agent_id)
        on.discard(agent_id)
    monkeypatch.setattr(kiro_crew, "join", join)
    monkeypatch.setattr(kiro_crew, "leave", leave)
    return {"dir": tmp_path / "crew", "on": on, "joined": joined, "left": left}


MODEL = {"model": "deepseek/deepseek-chat"}
USERS = '{\n  // mine\n  "agent": {"acp_backend": "kas", "name": "Crew"},\n  "ui": {"theme": "dark"}\n}\n'


def test_connect_runs_on_codex_and_disconnect_puts_back(crew) -> None:
    from openprogram.models.gateway.agents import engine
    path = crew["dir"] / "config.json"
    path.write_text(USERS)
    crew["on"].add("codex")
    engine.connect("kiro_crew", MODEL)
    doc = json.loads("".join(line for line in path.read_text().splitlines(True) if "//" not in line))
    assert doc["agent"] == {"acp_backend": "codex", "name": "Crew", "member_acp_backend": "codex"}
    assert doc["ui"] == {"theme": "dark"}
    assert "new sessions" in engine.status("kiro_crew")["notice"]
    engine.disconnect("kiro_crew")
    assert path.read_text() == USERS


def test_picks_a_harness(crew, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    path = crew["dir"] / "config.json"
    with pytest.raises(engine.AgentConfigError, match="connect Codex, Claude Code"):
        engine.connect("kiro_crew", MODEL)
    assert not path.exists()
    crew["on"].update({"codex", "claude_code"})
    monkeypatch.setattr(kiro_crew, "has_adapter", lambda h: h.id == "claude")
    engine.connect("kiro_crew", MODEL)
    assert json.loads(path.read_text())["agent"]["acp_backend"] == "claude"
    engine.disconnect("kiro_crew")
    monkeypatch.setattr(kiro_crew, "has_adapter", lambda h: False)
    engine.connect("kiro_crew", MODEL)
    assert json.loads(path.read_text())["agent"]["acp_backend"] == "codex"
    assert "npm i -g @agentclientprotocol/codex-acp" in engine.status("kiro_crew")["notice"]


def test_harness_pick(crew) -> None:
    from openprogram.models.gateway.agents import engine
    path = crew["dir"] / "config.json"
    crew["on"].update({"codex", "claude_code"})
    engine.connect("kiro_crew", {**MODEL, "harness": "claude"})
    assert json.loads(path.read_text())["agent"] == {"acp_backend": "claude", "member_acp_backend": "claude"}
    with pytest.raises(engine.AgentConfigError, match="isn't connected"):
        engine.connect("kiro_crew", {**MODEL, "harness": "goose"})
    options = [o["value"] for o in engine.status("kiro_crew")["fields"][1]["options"]]
    assert options == ["codex", "claude"]
    engine.disconnect("kiro_crew")
    assert not path.exists()


def test_local_pin_is_left_alone(crew) -> None:
    from openprogram.models.gateway.agents import engine
    crew["on"].add("codex")
    (crew["dir"] / "config.local.json").write_text('{"agent": {"acp_backend": "kas"}}')
    with pytest.raises(engine.AgentConfigError, match="config.local.json"):
        engine.connect("kiro_crew", MODEL)
    assert not (crew["dir"] / "config.json").exists()
    assert "config.local.json" in kiro_crew._notice()


def test_a_harness_set_since_is_kept(crew) -> None:
    from openprogram.models.gateway.agents import engine
    path = crew["dir"] / "config.json"
    path.write_text('{"agent": {"acp_backend": "kas"}}')
    crew["on"].add("codex")
    engine.connect("kiro_crew", MODEL)
    path.write_text('{"agent": {"acp_backend": "opencode", "member_acp_backend": "opencode"}}')
    engine.disconnect("kiro_crew")
    assert json.loads(path.read_text())["agent"] == {"acp_backend": "opencode", "member_acp_backend": "opencode"}


def test_broken_config_is_left_alone(crew) -> None:
    from openprogram.models.gateway.agents import engine
    path = crew["dir"] / "config.json"
    path.write_text('{"agent": ')
    crew["on"].add("codex")
    with pytest.raises(engine.AgentConfigError):
        engine.connect("kiro_crew", MODEL)
    assert path.read_text() == '{"agent": '


def test_connects_its_harness_and_takes_it_off_again(crew) -> None:
    from openprogram.models.gateway.agents import engine
    path = crew["dir"] / "config.json"
    path.write_text('{"agent": {"acp_backend": "claude"}}')
    engine.connect("kiro_crew", MODEL)
    assert crew["joined"] == [("claude_code", "deepseek/deepseek-chat")]
    assert "connected Claude Code too" in engine.status("kiro_crew")["notice"]
    engine.disconnect("kiro_crew")
    assert crew["left"] == ["claude_code"]
    assert json.loads(path.read_text()) == {"agent": {"acp_backend": "claude"}}


def test_a_harness_connected_before_stays(crew) -> None:
    from openprogram.models.gateway.agents import engine
    (crew["dir"] / "config.json").write_text('{"agent": {"acp_backend": "claude"}}')
    crew["on"].add("claude_code")
    engine.connect("kiro_crew", MODEL)
    engine.disconnect("kiro_crew")
    assert crew["joined"] == [] and crew["left"] == []


def test_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "elsewhere"))
    assert kiro_crew.home() == (tmp_path / "elsewhere").resolve()
    monkeypatch.setenv("KIROCREW_HOME", "~/crewhome")
    assert kiro_crew.home() == (tmp_path / "crewhome").resolve()
    if sys.platform != "win32":
        for unsafe in ("/", "/usr/local/crew", "/etc"):
            monkeypatch.setenv("KIROCREW_HOME", unsafe)
            assert kiro_crew.home() == tmp_path / ".kiro" / "crew"
    monkeypatch.delenv("KIROCREW_HOME")
    assert kiro_crew.home() == tmp_path / ".kiro" / "crew"


def test_finds_adapters(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kiro_crew, "find_tool", lambda name: "")
    codex = kiro_crew.harness_of("codex")
    env = {}
    assert not kiro_crew.finds(codex, tmp_path, env.get, windows=False)
    named = tmp_path / "somewhere" / "adapter"
    named.parent.mkdir()
    named.write_text("")
    env["CODEX_ACP_BIN"] = str(named)
    assert kiro_crew.finds(codex, tmp_path, env.get, windows=False)
    env.clear()
    node = tmp_path / ".nvm" / "versions" / "node" / "v22.1.0" / "bin"
    node.mkdir(parents=True)
    (node / "codex-acp").write_text("")
    assert kiro_crew.finds(codex, tmp_path, env.get, windows=False)
    other = tmp_path / ".local" / "bin"
    other.mkdir(parents=True)
    (other / "claude-agent-acp-old").write_text("")
    assert not kiro_crew.finds(kiro_crew.harness_of("claude"), tmp_path, env.get, windows=False)
    (other / "claude-agent-acp").write_text("")
    assert kiro_crew.finds(kiro_crew.harness_of("claude"), tmp_path, env.get, windows=False)
    assert os.path.isfile(other / "claude-agent-acp")
