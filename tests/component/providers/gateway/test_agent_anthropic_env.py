"""Claude Code's parity pieces, Paseo, NewMax, MiniMax Code and ZCode, after
yetone/magpie's tests of the same agents (``internal/agent/claude_dirs_test.go``,
``claude_subagent_test.go``, ``internal/gateway/claude_subagent_test.go``,
``claude_launch_test.go``, ``paseo_test.go``, ``newmax_test.go``,
``minimax_test.go``, ``zcode_test.go``; MIT License)."""
from __future__ import annotations

import json
import sqlite3

import pytest

GW = "http://127.0.0.1:18100"
ROWS = [
    {"id": "deepseek/pro", "name": "Pro", "provider": "deepseek", "context_window": 128000, "max_tokens": 8000,
     "vision": False, "thinking_levels": ["none", "low", "medium", "high"], "default_thinking_level": "high"},
    {"id": "deepseek/flash", "name": "Flash", "provider": "deepseek", "context_window": 64000, "max_tokens": None,
     "vision": True, "thinking_levels": [], "default_thinking_level": None},
]
MODULES = ("codex", "claude_code", "paseo", "newmax", "minimax", "zcode")


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / "home"
    user.mkdir()
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(user))
    for name in ("CLAUDE_CONFIG_DIR", "PASEO_HOME", "NEWMAX_USER_DATA_DIR", "MINIMAX_DATA_DIR", "MAVIS_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    from openprogram.models.gateway import address, agents, conversion
    monkeypatch.setattr(agents, "MODULES", MODULES)
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: GW)
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(row) for row in ROWS])
    from openprogram.models.gateway.agents import running
    monkeypatch.setattr(running, "list_running", lambda pattern: [])
    return user


def _json(path):
    return json.loads(path.read_text())


# Claude Code: more than one config folder (claude_dirs_test.go)

def test_claude_code_in_more_than_one_folder(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import all_agents, claude_code, engine, find
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text("{}")
    work = home / ".claude-work"
    work.mkdir()
    (work / "settings.json").write_text('{"theme": "dark"}')
    (home / ".claude2" / "projects" / "p").mkdir(parents=True)
    # not Claude Code's: another tool's folder, a file, a backup, the launch file's name
    (home / ".claude-code-router").mkdir()
    (home / ".claude-code-router" / "config.json").write_text("{}")
    (home / ".claude.json").write_text("{}")
    (home / ".claude.json.backup").write_text("{}")
    (home / ".claude-launch").mkdir()
    (home / ".claude-launch" / "settings.json").write_text("{}")

    rows = {a.id: a for a in claude_code.extra_agents()}
    assert set(rows) == {"claude_code#work", "claude_code#2"}
    assert "claude_code#work" in [a.id for a in all_agents()]
    row = find("claude_code#work")
    assert row.name == "Claude Code · work" and row.installed() and not row.harness and not row.ua

    engine.connect("claude_code#work", {"model": "deepseek/pro"})
    doc = _json(work / "settings.json")
    assert doc["env"]["ANTHROPIC_BASE_URL"] == GW + "/gateway/anthropic" and doc["theme"] == "dark"
    assert "env" not in _json(home / ".claude" / "settings.json")  # the default row is untouched
    engine.disconnect("claude_code#work")
    assert _json(work / "settings.json") == {"theme": "dark"}

    # with CLAUDE_CONFIG_DIR at one of them, the others (~/.claude too) are rows
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(work))
    assert {a.id for a in claude_code.extra_agents()} == {"claude_code#default", "claude_code#2"}


# Claude Code: subagents (claude_subagent_test.go, gateway/claude_subagent_test.go)

EXPLORE = {"model": "deepseek/pro", "max_tokens": 100, "tools": [{"name": "Read"}],
           "system": [{"type": "text", "text": "You are a file search specialist for Claude Code, …"}],
           "messages": [{"role": "user", "content": "find it"}]}


def test_claude_subagent_model_and_explore_turns(home) -> None:
    from openprogram.models.gateway import claude_subagent
    from openprogram.models.gateway.agents import engine
    (home / ".claude").mkdir()
    path = home / ".claude" / "settings.json"
    path.write_text('{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "inherit"}}')
    headers = {"x-claude-code-agent-id": "a1"}

    engine.connect("claude_code", {"model": "deepseek/pro"})
    # following the main model, the variable goes
    assert "CLAUDE_CODE_SUBAGENT_MODEL" not in _json(path)["env"]
    assert claude_subagent.subagent_model() == ""
    assert claude_subagent.move(EXPLORE, headers) is EXPLORE

    engine.connect("claude_code", {"model": "deepseek/pro", "subagent": "deepseek/flash"})
    assert _json(path)["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek/flash"
    assert claude_subagent.subagent_model() == "deepseek/flash"
    assert claude_subagent.move(EXPLORE, headers)["model"] == "deepseek/flash"
    # a lead's turn, a turn with no tools, a custom agent's prompt: left as asked
    assert claude_subagent.move(EXPLORE, {}) is EXPLORE
    assert claude_subagent.move({**EXPLORE, "tools": []}, headers)["model"] == "deepseek/pro"
    custom = {**EXPLORE, "system": "You are my reviewer."}
    assert claude_subagent.move(custom, headers) is custom

    # the user's own value isn't the gateway's to read, nor settings sent elsewhere
    doc = _json(path)
    doc["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "inherit"
    path.write_text(json.dumps(doc))
    assert claude_subagent.subagent_model() == ""
    doc["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "deepseek/flash"
    doc["env"]["ANTHROPIC_BASE_URL"] = "https://api.example.com"
    path.write_text(json.dumps(doc))
    assert claude_subagent.subagent_model() == ""

    engine.connect("claude_code", {"model": "deepseek/pro"})
    engine.disconnect("claude_code")
    assert _json(path)["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "inherit"


# Claude Code: OpenProgram's own chats on the gateway (claude_launch_test.go)

def test_claude_code_harness_runs_on_a_launch_file(home, monkeypatch) -> None:
    from types import SimpleNamespace

    from openprogram.models.gateway.agents import claude_code
    monkeypatch.setattr("openprogram.models.gateway.address.anthropic_base_url",
                        lambda stable=False: GW + "/gateway/anthropic")
    path = claude_code.launch_settings("openprogram:deepseek/pro", {"haiku": "deepseek/flash"}, "s1:main")
    doc = _json(path)
    assert doc["model"] == "deepseek/pro" and doc["env"]["ANTHROPIC_BASE_URL"] == GW + "/gateway/anthropic"
    assert doc["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek/flash"
    # what could take a request past the gateway is blanked over the user's own
    assert all(doc["env"][name] == "" for name in claude_code.LAUNCH_BLANKS)
    assert path.stat().st_mode & 0o077 == 0
    assert not (home / ".claude" / "settings.json").exists()
    # each chat has its own file, named by the header its requests carry
    other = claude_code.launch_settings("openprogram:deepseek/flash", {}, "s2:main")
    assert other != path and _json(path)["model"] == "deepseek/pro"
    launch = claude_code.launch_id("s1:main")
    assert doc["env"]["ANTHROPIC_CUSTOM_HEADERS"] == f"{claude_code.LAUNCH_HEADER}: {launch}"
    with pytest.raises(ValueError):
        claude_code.launch_path("../x")

    from openprogram.runtime.harnesses.claude_code import engine as cc_engine
    monkeypatch.setattr("openprogram.state.settings.setting_value", lambda key: {})
    turn = object.__new__(cc_engine.ClaudeTurn)
    turn.model_id = "openprogram:deepseek/pro"
    turn.req = SimpleNamespace(thinking_effort=None, permission_mode="bypass", session_id="s1")
    monkeypatch.setattr(turn.__class__, "_listed_efforts", lambda self: None)
    monkeypatch.setattr(turn.__class__, "_chat_key", lambda self: "s1:main")
    argv = turn._launch("claude", [], None)
    assert argv[argv.index("--settings") + 1] == str(path)
    assert argv[argv.index("--model") + 1] == "deepseek/pro"
    # another model is another process: its launch file was read at start
    marker = turn._gateway_marker()
    turn.model_id = "openprogram:deepseek/flash"
    assert turn._gateway_marker() != marker


def test_claude_launch_subagent_reads_the_chats_file(home, monkeypatch) -> None:
    from openprogram.models.gateway import claude_subagent
    from openprogram.models.gateway.agents import claude_code
    monkeypatch.setattr("openprogram.models.gateway.address.anthropic_base_url",
                        lambda stable=False: GW + "/gateway/anthropic")
    claude_code.launch_settings("openprogram:deepseek/pro", {"subagent": "deepseek/flash"}, "s1:main")
    claude_code.launch_settings("openprogram:deepseek/pro", {}, "s2:main")
    (home / ".claude").mkdir()
    # the user's own file, also on the gateway, isn't the launch chats' to follow
    (home / ".claude" / "settings.json").write_text(json.dumps({"env": {
        "ANTHROPIC_BASE_URL": GW + "/gateway/anthropic", "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek/pro"}}))
    one = {"x-claude-code-agent-id": "a", claude_code.LAUNCH_HEADER: claude_code.launch_id("s1:main")}
    two = {"x-claude-code-agent-id": "a", claude_code.LAUNCH_HEADER: claude_code.launch_id("s2:main")}
    assert claude_subagent.subagent_model(one) == "deepseek/flash"
    assert claude_subagent.subagent_model(two) == ""
    assert claude_subagent.subagent_model({claude_code.LAUNCH_HEADER: "../../x"}) == ""
    assert claude_subagent.subagent_model({}) == "deepseek/pro"


# ClaudeLaunchAfter (claude_launch_test.go)

def test_claude_launch_after_restores_the_users_model(home) -> None:
    from openprogram.models.gateway.agents import claude_code
    (home / ".claude").mkdir()
    path = home / ".claude" / "settings.json"
    path.write_text(json.dumps({"model": "opus", "theme": "dark"}))
    was = claude_code.launch_before()
    # Claude Code's own 1M rewrite at start goes back
    path.write_text(json.dumps({"model": "opus[1m]", "theme": "dark"}))
    claude_code.launch_after(was)
    assert _json(path) == {"model": "opus", "theme": "dark"}
    # a /model pick of OpenProgram's goes back; a Claude model of the user's stays
    path.write_text(json.dumps({"model": "deepseek/pro", "theme": "dark"}))
    claude_code.launch_after(was)
    assert _json(path)["model"] == "opus"
    path.write_text(json.dumps({"model": "sonnet"}))
    claude_code.launch_after(was)
    assert _json(path)["model"] == "sonnet"
    # no model before: the pick is taken out
    path.write_text("{}")
    was = claude_code.launch_before()
    path.write_text(json.dumps({"model": "deepseek/flash"}))
    claude_code.launch_after(was)
    assert _json(path) == {}
    # the user's file on the gateway: a pick of its models is one plain claude runs
    path.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": GW + "/gateway/anthropic"},
                                "model": "deepseek/flash"}))
    claude_code.launch_after(was)
    assert _json(path)["model"] == "deepseek/flash"


# Flat ids (provider/flat.go)

def test_flat_ids_are_taken_back(home) -> None:
    from openprogram.models.gateway.conversion import flat_id, unflat
    assert flat_id("deepseek/pro") == "deepseek~pro"
    assert unflat("deepseek~pro") == "deepseek/pro"
    assert unflat("deepseek/pro") == "" and unflat("plain") == "" and unflat("nobody~x") == ""


# Paseo (paseo_test.go)

def test_paseo_additional_models(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    folder = home / ".paseo"
    path = folder / "config.json"
    assert not find("paseo").installed()
    folder.mkdir()
    assert find("paseo").installed()
    user = {"version": 1, "daemon": {"listen": "127.0.0.1:6767"},
            "agents": {"providers": {"claude": {"additionalModels": [{"id": "mine/x", "label": "Mine"}],
                                                "env": {"A": "b"}}, "codex": {"enabled": True}}}}
    path.write_text(json.dumps(user))
    engine.connect("paseo", {"model": "deepseek/flash"})
    doc = _json(path)
    claude = doc["agents"]["providers"]["claude"]
    assert doc["daemon"]["listen"] == "127.0.0.1:6767" and claude["env"] == {"A": "b"}
    ids = [entry["id"] for entry in claude["additionalModels"]]
    assert ids == ["mine/x", "deepseek/flash", "deepseek/pro"]  # the user's first, the pick next
    assert all(entry["label"] and entry["description"].endswith("via OpenProgram")
               for entry in claude["additionalModels"][1:])
    assert not engine.sync("paseo")  # a sync changes nothing that is in step

    # Paseo rewrote its list since (an entry of the user's added): off still takes out only ours
    claude["additionalModels"].append({"id": "mine/y", "label": "Y"})
    path.write_text(json.dumps(doc))
    assert not engine.status("paseo")["config"]["drift"]
    engine.disconnect("paseo")
    assert [e["id"] for e in _json(path)["agents"]["providers"]["claude"]["additionalModels"]] == ["mine/x", "mine/y"]

    # on with no file at all, then off leaves nothing behind
    path.unlink()
    engine.connect("paseo", {"model": "deepseek/pro"})
    assert _json(path)["agents"]["providers"]["claude"]["additionalModels"]
    engine.disconnect("paseo")
    assert not path.exists() or "additionalModels" not in path.read_text()


# NewMax (newmax_test.go)

def _newmax_db(folder, app=None):
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "newmax.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE other (x)")
        if app is not None:
            conn.execute("INSERT INTO settings VALUES ('app', ?)", (app,))
    return db


def _row(db):
    with sqlite3.connect(db) as conn:
        hit = conn.execute("SELECT value FROM settings WHERE key = 'app'").fetchone()
    return hit[0] if hit else None


def test_newmax_provider_in_its_sqlite_row(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    from openprogram.models.gateway.agents.engine import AgentConfigError
    with pytest.raises(AgentConfigError, match="open NewMax once"):
        engine.connect("newmax", {"model": "deepseek/pro"})
    assert find("newmax") is not None and not find("newmax").installed()
    app = ('{"theme":"dark","providerId":"default","model":"default",'
           '"providers":[{"id":"default","name":"Default"}],"providerOrder":["default"]}')
    db = _newmax_db(home / ".newmax", app)
    assert find("newmax").installed()

    engine.connect("newmax", {"model": "deepseek/flash"})
    doc = json.loads(_row(db))
    entry = doc["providers"][1]
    assert doc["theme"] == "dark" and doc["providers"][0] == {"id": "default", "name": "Default"}
    assert entry["id"] == "openprogram-gateway" and entry["apiFormat"] == "anthropic"
    assert entry["baseUrl"] == GW + "/gateway/anthropic" and entry["apiKeyField"] == "ANTHROPIC_AUTH_TOKEN"
    assert entry["apiKey"].startswith("opgw-agent-newmax.") and entry["apiKeys"] == [entry["apiKey"]]
    assert entry["models"] == entry["enabledModels"] == ["deepseek/flash", "deepseek/pro"]
    assert entry["modelContextWindows"] == {"deepseek/flash": 64000, "deepseek/pro": 128000}
    assert doc["providerOrder"] == ["default", "openprogram-gateway"]

    # NewMax renamed it, turned it off and added what it tested; it then picked it
    entry.update({"name": "Mine", "enabled": False, "capabilities": {"tools": True}})
    doc["providerId"] = "openprogram-gateway"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'app'", (json.dumps(doc),))
    engine.connect("newmax", {"model": "deepseek/pro"})
    entry = json.loads(_row(db))["providers"][1]
    assert entry["name"] == "Mine" and entry["enabled"] is False and entry["capabilities"] == {"tools": True}
    assert entry["models"][0] == "deepseek/pro"

    engine.disconnect("newmax")
    after = json.loads(_row(db))
    assert [p["id"] for p in after["providers"]] == ["default"] and after["providerOrder"] == ["default"]
    assert after["providerId"] == "default" and after["model"] == "default" and after["theme"] == "dark"

    # a row OpenProgram made goes again
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM settings")
    engine.connect("newmax", {"model": "deepseek/pro"})
    assert json.loads(_row(db))["providerOrder"] == ["default", "openprogram-gateway"]
    engine.disconnect("newmax")
    assert _row(db) is None


def test_sqlite_row_read_leaves_nothing_beside_the_database(tmp_path) -> None:
    """A read writes no -shm or -wal beside another app's database, its WAL
    content still read (magpie sessions.OpenUntouched)."""
    from openprogram.models.gateway.edit import ConfigText, row_path
    db = _newmax_db(tmp_path / "app", '{"a":1}')
    assert ConfigText.read(row_path(db, "settings", "app")).text == '{"a":1}'
    assert sorted(p.name for p in db.parent.iterdir()) == ["newmax.db"]
    # the app holds it open in WAL mode with a change not yet checkpointed
    app = sqlite3.connect(db, isolation_level=None)
    app.execute("PRAGMA journal_mode=WAL")
    app.execute("PRAGMA wal_autocheckpoint=0")
    app.execute("UPDATE settings SET value = '{\"a\":2}' WHERE key = 'app'")
    before = sorted(p.name for p in db.parent.iterdir())
    assert ConfigText.read(row_path(db, "settings", "app")).text == '{"a":2}'
    assert sorted(p.name for p in db.parent.iterdir()) == before
    app.close()


def test_newmax_refused_while_it_runs(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, newmax
    from openprogram.models.gateway.agents.engine import AgentConfigError
    db = _newmax_db(home / ".newmax", "{}")
    monkeypatch.setattr(newmax, "_running", lambda: True)
    with pytest.raises(AgentConfigError, match="quit NewMax"):
        engine.connect("newmax", {"model": "deepseek/pro"})
    assert _row(db) == "{}"


# MiniMax Code (minimax_test.go)

MCODE = """# mine
defaultModel: minimax/MiniMax-M2.7
defaultModelVariant: thinking
custom_provider:
  other:
    name: other
provider:
  minimax:
    models:
      MiniMax-M2.7: {}
"""


def test_minimax_code_custom_provider(home) -> None:
    from openprogram.models.gateway import edit
    from openprogram.models.gateway.agents import engine
    folder = home / ".minimax"
    folder.mkdir()
    path = folder / "config.yaml"
    path.write_text(MCODE)
    engine.connect("minimax_code", {"model": "deepseek/pro"})
    text = path.read_text()
    doc = edit.load("yaml", text)
    assert text.startswith("# mine\n")
    assert doc["defaultModel"] == "custom_provider:openprogram/deepseek~pro" and "defaultModelVariant" not in doc
    entry = doc["custom_provider"]["openprogram"]
    assert doc["custom_provider"]["other"] == {"name": "other"}
    assert entry["api"] == "anthropic-messages" and entry["options"]["baseURL"] == GW + "/gateway/anthropic"
    assert entry["options"]["headers"]["User-Agent"] == "minimax-code"
    pro, flash = entry["models"]["deepseek~pro"], entry["models"]["deepseek~flash"]
    assert pro["limit"] == {"context": 128000, "output": 8000} and pro["reasoning"] is True
    assert pro["thinking"] == {"effortOptions": ["low", "medium", "high"], "defaultEffort": "high"}
    assert flash["reasoning"] is False and flash["capabilities"] == {"support_image": True}
    assert not engine.status("minimax_code")["config"]["error"]

    # a default effort set by hand stays; a key MiniMax Code put on the entry stays
    entry["models"]["deepseek~pro"]["thinking"]["defaultEffort"] = "low"
    entry["timeout"] = 30
    path.write_text(edit.edit("yaml", path.read_text(), sets={"custom_provider.openprogram": entry}))
    engine.sync("minimax_code")
    entry = edit.load("yaml", path.read_text())["custom_provider"]["openprogram"]
    assert entry["models"]["deepseek~pro"]["thinking"]["defaultEffort"] == "low" and entry["timeout"] == 30

    engine.disconnect("minimax_code")
    doc = edit.load("yaml", path.read_text())
    assert doc["defaultModel"] == "minimax/MiniMax-M2.7" and doc["defaultModelVariant"] == "thinking"
    assert doc["custom_provider"] == {"other": {"name": "other"}}


# ZCode (zcode_test.go)

def test_zcode_both_files(home) -> None:
    from openprogram.models.gateway.agents import engine
    v2 = home / ".zcode" / "v2"
    v2.mkdir(parents=True)
    config, rules = v2 / "config.json", v2 / "provider_config.json"
    config.write_text(json.dumps({"provider": {"zhipu": {"name": "Zhipu"}}, "theme": "dark"}))
    rules.write_text(json.dumps({"schemaVersion": 1, "config": {
        "providerConfigRules": {"providerRules": [{"providerId": "zhipu"}]},
        "modelConfigRules": {"providerModelRules": [{"providerId": "zhipu", "modelId": "glm"}],
                             "manualProviderModelRules": [{"providerId": "openprogram", "modelId": "deepseek/flash",
                                                           "config": {"mine": True}}]}}}))
    engine.connect("zcode", {"model": "deepseek/pro"})
    entry = _json(config)["provider"]["openprogram"]
    assert entry["kind"] == "anthropic" and entry["options"]["baseURL"] == GW + "/gateway/anthropic"
    pro = entry["models"]["deepseek/pro"]
    assert pro["reasoning"]["levels"] == ["disabled", "low", "medium", "high"]
    assert pro["reasoning"]["defaultLevel"] == "medium"
    assert pro["reasoning"]["providerOptionsByLevel"]["disabled"] == {"thinking": {"type": "disabled"}}
    assert pro["reasoning"]["providerOptionsByLevel"]["high"]["thinking"]["type"] == "enabled"
    assert entry["models"]["deepseek/flash"]["modalities"]["input"] == ["text", "image"]
    doc = _json(rules)
    providers = doc["config"]["providerConfigRules"]["providerRules"]
    assert [p["providerId"] for p in providers] == ["zhipu", "openprogram"]
    ours = providers[1]["config"]
    assert ours["api"] == {"type": "anthropic-messages", "baseUrl": GW + "/gateway/anthropic"}
    assert ours["personalModelIds"] == ["deepseek/pro", "deepseek/flash"]
    models = doc["config"]["modelConfigRules"]["providerModelRules"]
    # the model set by hand keeps its manual rule and gets none of OpenProgram's
    assert [(m["providerId"], m["modelId"]) for m in models] == [("zhipu", "glm"), ("openprogram", "deepseek/pro")]
    assert models[1]["config"]["properties"]["contextWindow"] == 128000

    # the user picked Chat Completions and renamed it in ZCode: a sync keeps both
    providers[1]["config"]["api"]["type"] = "openai-chat-completions"
    providers[1]["providerName"] = "Mine"
    rules.write_text(json.dumps(doc))
    engine.sync("zcode")
    ours = _json(rules)["config"]["providerConfigRules"]["providerRules"][1]
    assert ours["providerName"] == "Mine" and ours["config"]["api"] == {
        "type": "openai-chat-completions", "baseUrl": GW + "/gateway/openai/v1"}
    assert _json(config)["provider"]["openprogram"]["kind"] == "openai-compatible"

    engine.disconnect("zcode")
    assert _json(config) == {"provider": {"zhipu": {"name": "Zhipu"}}, "theme": "dark"}
    doc = _json(rules)["config"]
    assert doc["providerConfigRules"]["providerRules"] == [{"providerId": "zhipu"}]
    assert doc["modelConfigRules"]["providerModelRules"] == [{"providerId": "zhipu", "modelId": "glm"}]
    assert doc["modelConfigRules"]["manualProviderModelRules"] == []


def test_zcode_native_web_search(home, monkeypatch) -> None:
    """Each model whose provider searches gets ZCode's native web search; one
    unticked in ZCode stays unticked through syncs, a value in the rule stays,
    and on another format OpenProgram's on goes (zcode_websearch_test.go)."""
    from openprogram.models.gateway.agents import engine, zcode
    lite = {**ROWS[1], "id": "deepseek/lite", "name": "Lite"}
    nosearch = {**ROWS[1], "id": "other/x", "name": "X"}
    from openprogram.models.gateway import conversion
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(r) for r in (*ROWS, lite, nosearch)])
    monkeypatch.setattr(zcode, "searches", lambda model: not model.startswith("other/"))
    v2 = home / ".zcode" / "v2"
    v2.mkdir(parents=True)
    rules = v2 / "provider_config.json"
    rules.write_text(json.dumps({"schemaVersion": 1, "config": {"modelConfigRules": {"manualProviderModelRules": [
        {"providerId": "openprogram", "modelId": "deepseek/lite",
         "config": {"properties": {"supportsNativeWebSearch": False}}}]}}}))

    def search():
        doc = _json(rules)["config"]["modelConfigRules"]
        out = {}
        for rule in doc["providerModelRules"]:
            out[rule["modelId"].split("/")[1]] = rule["config"]["properties"].get("supportsNativeWebSearch")
        return out

    def change(model, fn):
        doc = _json(rules)
        for rule in doc["config"]["modelConfigRules"]["providerModelRules"]:
            if rule["modelId"] == model:
                fn(rule["config"]["properties"])
        rules.write_text(json.dumps(doc))

    def api(kind):
        doc = _json(rules)
        for rule in doc["config"]["providerConfigRules"]["providerRules"]:
            if rule["providerId"] == "openprogram":
                rule["config"]["api"]["type"] = kind
        rules.write_text(json.dumps(doc))

    engine.connect("zcode", {"model": "deepseek/pro"})
    # every model whose provider searches; the manual rule is the user's; no search, no key
    assert search() == {"pro": True, "flash": True, "x": None}
    change("deepseek/flash", lambda p: p.pop("supportsNativeWebSearch"))  # unticked in ZCode
    for _ in range(2):
        engine.sync("zcode")
        assert search() == {"pro": True, "flash": None, "x": None}
    change("deepseek/flash", lambda p: p.update(supportsNativeWebSearch=True))  # ticked again
    engine.sync("zcode")
    assert search()["flash"] is True
    # on Chat Completions ZCode can't send the search: OpenProgram's on goes
    api("openai-chat-completions")
    engine.sync("zcode")
    assert search() == {"pro": None, "flash": None, "x": None}
    api("anthropic-messages")
    engine.sync("zcode")
    assert search() == {"pro": True, "flash": True, "x": None}
    # an unreadable record is unknown: nothing turned on, the rules' values stay
    change("deepseek/flash", lambda p: p.pop("supportsNativeWebSearch"))
    zcode.searched_path().write_text("{not json")
    engine.sync("zcode")
    assert search() == {"pro": True, "flash": None, "x": None}
    engine.disconnect("zcode")
    assert not zcode.searched_path().exists()


def test_gateway_asks_the_provider_to_search(home, monkeypatch) -> None:
    """Anthropic's web_search server tool asks a provider that searches to;
    a tool_choice naming it is no function's to force."""
    from openprogram.models.gateway import conversion, messages
    from openprogram.models.providers.types import Model
    model = Model(id="m", name="m", api="anthropic-messages", provider="anthropic", base_url="",
                  context_window=1000, max_tokens=100)
    monkeypatch.setattr(conversion, "lookup_model", lambda ref: model)
    body = {"model": "anthropic/m", "max_tokens": 10, "messages": [{"role": "user", "content": "news?"}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            "tool_choice": {"type": "tool", "name": "web_search"}}
    monkeypatch.setattr(conversion, "searches", lambda m: True)
    _, context, options, _ = messages.parse_request(body)
    assert options.web_search is True and options.tool_choice is None and not context.tools
    monkeypatch.setattr(conversion, "searches", lambda m: False)
    assert messages.parse_request(body)[2].web_search is None
    assert messages.parse_request({**body, "tools": []})[2].tool_choice == {"type": "function", "name": "web_search"}


def test_registry_rows(home) -> None:
    from openprogram.models.gateway.agents import find
    assert find("mcode").id == "minimax_code" and find("z-code").id == "zcode"
    assert find("getpaseo").id == "paseo" and find("newmax-ai").id == "newmax"
    assert [f.key for f in find("claude_code").fields][-1] == "subagent"
