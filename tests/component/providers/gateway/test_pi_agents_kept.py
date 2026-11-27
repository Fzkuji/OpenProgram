"""What the agents in Pi's format keep between writes, their pickers, and
Craft Agents, after yetone/magpie's tests of them (``scope_gone_test.go``,
``liveagent_test.go``, ``aside_native_test.go``, ``aside_account_test.go``,
``pi_protocol_test.go``, ``omp_protocol_test.go``, ``omp_advisor_test.go``,
``omp_role_thinking_test.go``, ``craftagents_test.go``; MIT License,
Copyright (c) yetone)."""
from __future__ import annotations

import pytest

from .test_pi_agents import GW, MODELS, _json, _write, home  # noqa: F401 - the fixture


def _serve(monkeypatch, ids):
    from openprogram.models.gateway import conversion
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS if m["id"] in ids])


ALL = [m["id"] for m in MODELS]


# enabledModels after a sync (scope_gone.go)

def test_scope_synced_rules() -> None:
    from openprogram.models.gateway.agents.pi_like import scope_synced
    offered = {"openprogram/a/x"}
    # an entry on a model no longer given goes, and is remembered; globs and others stay
    nxt, has, mem, changed = scope_synced(["openprogram/a/x", "openprogram/b/y:high", "openprogram/*", "mine/z"],
                                          True, offered, {})
    assert nxt == ["openprogram/a/x", "openprogram/*", "mine/z"] and has and changed
    assert mem == {"entries": ["openprogram/b/y:high"]}
    # it comes back on the end once given again
    nxt, _, mem, _ = scope_synced(nxt, True, offered | {"openprogram/b/y"}, mem)
    assert nxt[-1] == "openprogram/b/y:high" and mem == {}
    # a list left with nothing goes, and comes back with its entries
    nxt, has, mem, changed = scope_synced(["openprogram/b/y"], True, offered, {})
    assert nxt == [] and not has and changed and mem == {"entries": ["openprogram/b/y"], "emptied": True}
    nxt, has, _, _ = scope_synced([], False, offered | {"openprogram/b/y"}, mem)
    assert nxt == ["openprogram/b/y"] and has
    # a list the user removed isn't made again
    assert scope_synced([], False, offered, {"entries": ["openprogram/a/x"]})[1] is False


def test_pi_scope_follows_the_catalog(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    settings = home / ".pi" / "agent" / "settings.json"
    _write(settings, {"enabledModels": ["mine/m", "openprogram/openai/gpt-5.5:high"]})
    engine.connect("pi", {"model": "deepseek/deepseek-chat"})
    assert _json(settings)["enabledModels"] == ["mine/m", "openprogram/openai/gpt-5.5:high",
                                                "openprogram/deepseek/deepseek-chat"]
    _serve(monkeypatch, ["deepseek/deepseek-chat"])
    assert engine.sync("pi")
    assert _json(settings)["enabledModels"] == ["mine/m", "openprogram/deepseek/deepseek-chat"]
    # the user's own edit of the list is no drift and leaves the sync running
    data = _json(settings)
    data["enabledModels"].append("mine/n")
    _write(settings, data)
    assert engine.owned("pi") and engine.status("pi")["config"]["drift"] == []
    _serve(monkeypatch, ALL)
    assert engine.sync("pi")
    assert _json(settings)["enabledModels"] == ["mine/m", "openprogram/deepseek/deepseek-chat", "mine/n",
                                                "openprogram/openai/gpt-5.5:high"]
    engine.disconnect("pi")


def test_stepcode_scope_list_goes_and_comes_back(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    config = home / ".stepcode" / "config.toml"
    _write(config, 'enabledModels = ["openprogram/openai/gpt-5.5"]\n')
    engine.connect("stepcode", {"model": "openai/gpt-5.5"})
    _serve(monkeypatch, ["deepseek/deepseek-chat"])
    engine.sync("stepcode")
    assert "enabledModels" not in config.read_text()
    _serve(monkeypatch, ALL)
    engine.sync("stepcode")
    assert 'enabledModels = ["openprogram/openai/gpt-5.5"]' in config.read_text()
    engine.disconnect("stepcode")
    # with OpenProgram's provider gone its entries would name nothing, and a
    # list left with none goes (magpie piScopeWithout)
    assert "enabledModels" not in config.read_text()


def test_omp_scope_keeps_folder_entries(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import load
    config = home / ".omp" / "agent" / "config.yml"
    _write(config, "enabledModels:\n  - mine/m\n  - openprogram/openai/gpt-5.5:high\n  - path: /work\n    models: [x/y]\n")
    engine.connect("omp", {"model": "mine/m"})
    _serve(monkeypatch, ["deepseek/deepseek-chat"])
    engine.sync("omp")
    scope = load("yaml", config.read_text(), "config.yml")["enabledModels"]
    assert scope == ["mine/m", {"path": "/work", "models": ["x/y"]}]
    _serve(monkeypatch, ALL)
    engine.sync("omp")
    scope = load("yaml", config.read_text(), "config.yml")["enabledModels"]
    assert scope[-1] == "openprogram/openai/gpt-5.5:high"


# LiveAgent's memory of what the user took out

def test_liveagent_keeps_out_what_the_user_deleted(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, liveagent
    monkeypatch.setattr(liveagent, "pids", lambda: "")
    path = home / ".liveagent" / "config.json"
    _write(path, {"providers": {}})
    engine.connect("liveagent", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    # the user deletes a model and the whole Anthropic provider in LiveAgent
    data["providers"]["openprogram"]["models"] = [m for m in data["providers"]["openprogram"]["models"]
                                                  if m["id"] != "google/gemini-3-pro"]
    del data["providers"]["openprogram-anthropic"]
    _write(path, data)
    assert engine.owned("liveagent")
    _serve(monkeypatch, ALL[:3])
    engine.sync("liveagent")
    _serve(monkeypatch, ALL)
    engine.sync("liveagent")
    data = _json(path)
    assert [m["id"] for m in data["providers"]["openprogram"]["models"]] == ["deepseek/deepseek-chat"]
    assert "openprogram-anthropic" not in data["providers"]
    engine.disconnect("liveagent")
    assert not any(k.startswith("openprogram") for k in _json(path)["providers"])
    # turned off and on, it starts over
    engine.connect("liveagent", {"model": "deepseek/deepseek-chat"})
    assert "openprogram-anthropic" in _json(path)["providers"]


def test_liveagent_pending_while_it_runs(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import liveagent
    kept: dict = {}
    olds = {"openprogram": {"models": [{"id": "a"}]}}
    grouped = {"openprogram": [{"id": "a"}, {"id": "b"}]}
    liveagent.wrote(kept, olds, grouped, "11")
    assert kept["had"] == ["k:openprogram", "m:a"] and kept["pending"] == {"m:b": "11"}
    # its save dropped b, which it never read; started again since, it counts as had
    out, _, _ = liveagent.take(kept, olds, grouped, {"a": "openprogram", "b": "openprogram"}, "11")
    assert [m["id"] for m in out["openprogram"]] == ["a", "b"]
    out, _, _ = liveagent.take(kept, olds, grouped, {}, "12")
    assert "m:b" in kept["gone"] and [m["id"] for m in out["openprogram"]] == ["a"]


# Aside

def test_aside_picked_account_and_live_apply(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import aside, engine
    for n in (0, 1):
        _write(home / ".aside" / "u" / str(n) / "settings.json", {"defaultModel": {"provider": "x", "modelId": "y"}})
    _write(home / ".aside" / "accounts.json", {"currentAccountId": 0})
    assert aside.account_options() == []
    _write(home / ".aside" / "accounts.json", {"currentAccountId": 0, "accounts": [
        {"id": 1, "email": "b@example.com"}, {"id": 0}]})
    assert aside.account_options() == [("u0", "u0 · Local Account · active in Aside"), ("u1", "u1 · b@example.com")]
    aside.pick("u1")
    assert aside.folder() == home / ".aside" / "u" / "1"
    with pytest.raises(ValueError):
        aside.pick("u9")
    calls = []
    monkeypatch.setattr(aside, "repl", lambda account, expr: calls.append((account, expr)) or True)
    engine.connect("aside", {"model": "deepseek/deepseek-chat", "fast": "openai/gpt-5.5", "account": "u1",
                             "effort": "high"})
    account, expr = calls[-1]
    assert account == 1 and '"provider": "openprogram"' in expr and 'c["fast"]=' in expr
    assert _json(home / ".aside" / "u" / "1" / "settings.json")["defaultModel"]["thinkingLevel"] == "high"
    engine.disconnect("aside")
    account, expr = calls[-1]
    assert '"modelId": "y"' in expr and 'delete c["fast"]' in expr
    aside.pick("")
    assert aside.folder() == home / ".aside" / "u" / "0"


# Craft Agents

def test_craft_agents_connection(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.agents.engine import AgentConfigError
    from openprogram.models.gateway.keys import path_key, token_for
    path = home / ".craft-agent" / "config.json"
    with pytest.raises(AgentConfigError, match="open Craft Agents once"):
        engine.connect("craft-agents", {"model": "deepseek/deepseek-chat"})
    mine = {"slug": "mine", "name": "Mine", "providerType": "anthropic"}
    _write(path, {"workspaces": [], "llmConnections": [mine], "defaultLlmConnection": "mine"})
    engine.connect("craft-agents", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    ours = data["llmConnections"][1]
    assert data["llmConnections"][0] == mine and data["defaultLlmConnection"] == "openprogram"
    assert ours["authType"] == "none" and ours["providerType"] == "pi_compat"
    assert ours["baseUrl"] == f"{GW}/agent/{path_key(token_for('craft-agents'))}/openai/v1"
    assert ours["defaultModel"] == "deepseek/deepseek-chat" and len(ours["models"]) == len(MODELS)
    # Craft writes the user's own connections as they change; that's no
    # drift, and the name they gave OpenProgram's stays through a sync
    data["llmConnections"].append({"slug": "new", "name": "New"})
    data["llmConnections"][1]["name"] = "Gateway"
    _write(path, data)
    assert engine.owned("craft-agents")
    assert engine.status("craft-agents")["config"]["drift"] == []
    engine.sync("craft-agents")
    assert [c["name"] for c in _json(path)["llmConnections"]] == ["Mine", "New", "Gateway"]
    engine.disconnect("craft-agents")
    data = _json(path)
    assert [c["slug"] for c in data["llmConnections"]] == ["mine", "new"] and data["defaultLlmConnection"] == "mine"


def test_craft_agents_default_goes_with_the_users_connection(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".craft-agent" / "config.json"
    mine = {"slug": "mine", "name": "Mine", "providerType": "anthropic"}
    _write(path, {"workspaces": [], "llmConnections": [mine], "defaultLlmConnection": "mine"})
    engine.connect("craft-agents", {"model": "deepseek/deepseek-chat"})
    data = _json(path)
    data["llmConnections"] = [c for c in data["llmConnections"] if c["slug"] != "mine"]
    _write(path, data)
    engine.disconnect("craft-agents")
    data = _json(path)
    # no default naming a connection that is gone: Craft picks its first
    assert "defaultLlmConnection" not in data and data["llmConnections"] == []


def test_craft_agents_key_in_its_address_opens_the_gateway(home) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.models.gateway.keys import path_key, token_for

    app = FastAPI()

    @app.get("/gateway/openai/v1/models")
    def models():
        return {"data": []}

    state = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                          bind_host="127.0.0.1", port=18100, allowed_origins=())
    guarded = OwnerAuthMiddleware(app, auth_state=state)
    segment = path_key(token_for("craft-agents"))
    with TestClient(guarded, base_url="http://127.0.0.1:18100", client=("127.0.0.1", 50000)) as client:
        # Craft's Pi sends a placeholder bearer; its address carries its key
        ok = client.get(f"/gateway/agent/{segment}/openai/v1/models", headers={"Authorization": "Bearer not-needed"})
        assert ok.status_code == 200
        assert client.get("/gateway/agent/craft-agents/openai/v1/models").status_code == 401


# Pickers

def test_pi_thinking_and_protocol(home) -> None:
    from openprogram.models.gateway.agents import engine
    settings, models = home / ".pi" / "agent" / "settings.json", home / ".pi" / "agent" / "models.json"
    rows = {row["key"]: row for row in engine.status("pi")["fields"]}
    assert [o["value"] for o in rows["effort"]["options"]][:2] == ["off", "minimal"]
    assert [o["value"] for o in rows["protocol"]["options"]] == ["openai-completions", "openai-responses",
                                                               "anthropic-messages"]
    assert "protocol" not in {row["key"] for row in engine.status("omo")["fields"]}
    _write(models, {"providers": {"openprogram": {"api": "openai-responses"}}})
    engine.connect("pi", {"model": "deepseek/deepseek-chat", "effort": "high"})
    assert _json(settings)["defaultThinkingLevel"] == "high"
    # an api the user set stays while no protocol is picked
    assert _json(models)["providers"]["openprogram"]["api"] == "openai-responses"
    engine.connect("pi", {"model": "deepseek/deepseek-chat", "protocol": "anthropic-messages"})
    block = _json(models)["providers"]["openprogram"]
    assert block["api"] == "anthropic-messages"
    assert {m["api"] for m in block["models"]} == {"anthropic-messages"}
    assert {m["baseUrl"] for m in block["models"]} == {GW + "/anthropic"}
    engine.disconnect("pi")
    assert not settings.exists()


def test_mothx_thinking(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".mothx" / "settings.json"
    engine.connect("mothx", {"model": "deepseek/deepseek-chat", "effort": "max"})
    assert _json(path)["defaultThinkingLevel"] == "max"
    engine.disconnect("mothx")


def test_omp_pickers(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import load
    folder = home / ".omp" / "agent"
    _write(folder / "config.yml", "modelRoles:\n  slow: xai/grok-4.6:high\n")
    engine.connect("omp", {"model": "deepseek/deepseek-chat", "slow": "openai/gpt-5.5", "slow_thinking": "low",
                           "effort": "auto", "advisor": "openai/gpt-5.5", "advisor_review": "agent-end",
                           "advisor_wait": "1", "advisor_wake": "blocker", "protocol": "anthropic-messages"})
    data = load("yaml", (folder / "config.yml").read_text())
    assert data["modelRoles"]["slow"] == "openprogram/openai/gpt-5.5:low"
    assert data["defaultThinkingLevel"] == "auto"
    # omp's enum settings take strings only
    assert data["advisor"] == {"enabled": True, "reviewMode": "agent-end", "syncBacklog": "1",
                               "continueSeverity": "blocker"}
    block = load("yaml", (folder / "models.yml").read_text())["providers"]["openprogram"]
    by_id = {m["id"]: m for m in block["models"]}
    assert block["api"] == "anthropic-messages"
    assert by_id["openai/gpt-5.5"]["api"] == "anthropic-messages"
    assert by_id["openai/gpt-5.5"]["baseUrl"] == GW + "/anthropic"
    assert by_id["openai/gpt-5.5"]["thinking"]["mode"] == "budget"
    assert by_id["anthropic/claude-opus-4-6"]["thinking"]["mode"] == "anthropic-adaptive"
    engine.connect("omp", {"model": "deepseek/deepseek-chat", "advisor_review": "off", "protocol": "openai-responses"})
    data = load("yaml", (folder / "config.yml").read_text())
    assert data["advisor"]["enabled"] is False
    block = load("yaml", (folder / "models.yml").read_text())["providers"]["openprogram"]
    assert block["api"] == "openai-responses" and all("api" not in m for m in block["models"])
    assert {m["thinking"]["mode"] for m in block["models"] if "thinking" in m} == {"effort"}
    engine.disconnect("omp")
    assert (folder / "config.yml").read_text() == "modelRoles:\n  slow: xai/grok-4.6:high\n"
