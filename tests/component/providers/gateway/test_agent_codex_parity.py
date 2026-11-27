"""Codex's profile wiring, subagent and memory models, background compaction,
the keys that hold its compaction, and the Codex app's error page after its
app-server ended, after yetone/magpie's ``codex_profile_test.go``,
``codex_memories_test.go``, ``codex_async_test.go``,
``codex_compact_held_test.go`` and ``codex_app_crash_test.go`` (MIT)."""
from __future__ import annotations

import os
import time
import tomllib
from datetime import datetime, timezone

import pytest

from openprogram.models.gateway.agents import codex as codex_agent


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr("openprogram.models.gateway.address.worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    (tmp_path / "codex").mkdir()
    from openprogram.models.gateway import codex_app
    monkeypatch.setattr(codex_app, "base_instructions", lambda _versions: "You are Codex.")
    monkeypatch.setattr(codex_app, "restart_app", lambda home: None)
    monkeypatch.setattr(codex_app, "move_history", lambda *a: 0)
    monkeypatch.setattr("openprogram.models.gateway.conversion.enabled_model_refs", lambda: [
        {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "context_window": 128000},
        {"id": "openrouter/x", "name": "X", "provider": "openrouter", "context_window": None},
    ])
    monkeypatch.setattr(codex_agent, "codex_readers", lambda: [codex_agent.CodexReader("cli", "0.170.0")])
    return tmp_path


def _toml(path):
    return tomllib.loads(path.read_text())


# Profiles (codex_profile_test.go)

def test_profile_only_leaves_config_toml_alone(homes) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    mine = '# mine\nmodel = "gpt-5.5"\n'
    config.write_text(mine)
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "scope": "profile"})
    profile = homes / "codex" / "openprogram.config.toml"
    doc = _toml(profile)
    assert doc["model_provider"] == "openprogram" and doc["model"] == "deepseek/deepseek-chat"
    assert doc["model_catalog_json"] == "openprogram-models.json"
    assert doc["model_providers"]["openprogram"]["base_url"].endswith("/gateway/openai/v1")
    assert (homes / "codex" / "openprogram-models.json").is_file()
    assert config.read_text() == mine
    # a switch of model touches the profile only
    engine.connect("codex", {"model": "openrouter/x", "scope": "profile"})
    assert _toml(profile)["model"] == "openrouter/x" and config.read_text() == mine
    assert "codex -p openprogram" in engine.status("codex")["notice"]
    engine.disconnect("codex")
    assert not profile.exists() and not (homes / "codex" / "openprogram-models.json").exists()
    assert config.read_text() == mine


def test_scope_waits_for_disconnect(homes) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    engine.connect("codex", {"model": "deepseek/deepseek-chat"})
    assert _toml(config)["model_provider"] == "openprogram"
    with pytest.raises(engine.AgentConfigError, match="turn Codex off"):
        engine.connect("codex", {"model": "deepseek/deepseek-chat", "scope": "profile"})
    assert not (homes / "codex" / "openprogram.config.toml").exists()
    engine.disconnect("codex")
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "scope": "profile"})
    assert (homes / "codex" / "openprogram.config.toml").exists()
    field = codex_agent.agent().field("scope")
    assert field.options() == [("profile", "codex -p openprogram only")]
    assert engine.status("codex")["fields"][-2]["options"] == [{"value": "profile", "label": "codex -p openprogram only"}]


# Subagent and memory models (codex_memories_test.go)

def test_memories_and_subagent_models(homes) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    config.write_text('[memories]\nuse_memories = true\n\n[agents.reviewer]\ndescription = "reviews"\n')
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "memories": "openrouter/x", "subagent": "openrouter/x"})
    doc = _toml(config)
    assert doc["memories"] == {"use_memories": True, "extract_model": "openrouter/x", "consolidation_model": "openrouter/x"}
    assert doc["agents"]["default_subagent_model"] == "openrouter/x"
    assert doc["agents"]["reviewer"] == {"description": "reviews"}
    # Default takes them out, the user's other [memories] settings kept
    engine.connect("codex", {"model": "deepseek/deepseek-chat"})
    doc = _toml(config)
    assert doc["memories"] == {"use_memories": True} and "default_subagent_model" not in doc["agents"]
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "memories": "openrouter/x"})
    engine.disconnect("codex")
    doc = _toml(config)
    assert doc["memories"] == {"use_memories": True} and doc["agents"] == {"reviewer": {"description": "reviews"}}


def test_memories_unwire_puts_back_the_users(homes) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    config.write_text('[memories]\nextract_model = "gpt-5.6-luna"\n')
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "memories": "openrouter/x"})
    assert _toml(config)["memories"]["extract_model"] == "openrouter/x"
    engine.disconnect("codex")
    assert _toml(config)["memories"] == {"extract_model": "gpt-5.6-luna"}


def test_a_model_leaving_moves_a_subagent_not_the_scope(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, lifecycle
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "subagent": "openrouter/x", "scope": ""})
    moves = lifecycle.reseat(["deepseek/deepseek-chat"])
    assert [(m.field, m.to) for m in moves] == [("subagent", "")]
    assert "default_subagent_model" not in _toml(homes / "codex" / "config.toml").get("agents", {})


# Background compaction (codex_async_test.go)

def test_async_compaction_written_and_taken_out(homes) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    config.write_text('model = "gpt-5.5"\n')
    on = {"async": "50"}
    engine.connect("codex", {"model": "deepseek/deepseek-chat", **on})
    doc = _toml(config)
    assert doc["features"]["async_compaction"] == {"enabled": True, "model_async_compact_threshold_percent": 50}
    assert doc["model_providers"]["openprogram"]["capabilities"] == {"remote_compaction": "v2"}
    # a model switch writing the table again keeps it
    engine.connect("codex", {"model": "openrouter/x", **on})
    assert _toml(config)["features"]["async_compaction"]["enabled"] is True
    # off takes all of it out again
    engine.connect("codex", {"model": "openrouter/x"})
    doc = _toml(config)
    assert "features" not in doc and "capabilities" not in doc["model_providers"]["openprogram"]
    engine.disconnect("codex")
    assert _toml(config)["model"] == "gpt-5.5"


def test_async_compaction_keeps_the_users(homes) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    config.write_text("[features]\nasync_compaction = true\n")
    # the user's own feature gets the capability, the option off
    engine.connect("codex", {"model": "deepseek/deepseek-chat"})
    doc = _toml(config)
    assert doc["features"]["async_compaction"] is True
    assert doc["model_providers"]["openprogram"]["capabilities"] == {"remote_compaction": "v2"}
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "async": "on"})
    assert _toml(config)["features"]["async_compaction"] is True  # never written over
    engine.disconnect("codex")
    assert _toml(config)["features"] == {"async_compaction": True}


def test_async_compaction_keeps_users_capability(homes) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    config.write_text('[model_providers.openprogram]\ncapabilities = { remote_compaction = "v1" }\n')
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "async": "on"})
    assert _toml(config)["model_providers"]["openprogram"]["capabilities"] == {"remote_compaction": "v1"}


def test_old_codex_gets_the_bare_key(homes, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    config = homes / "codex" / "config.toml"
    monkeypatch.setattr(codex_agent, "codex_readers", lambda: [codex_agent.CodexReader("app", "0.162.1")])
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "async": "50"})
    assert _toml(config)["features"] == {"async_compaction": True}
    # every Codex new enough again: the table comes back in place of the bare key
    monkeypatch.setattr(codex_agent, "codex_readers", lambda: [codex_agent.CodexReader("cli", "0.163.0")])
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "async": "50"})
    assert _toml(config)["features"]["async_compaction"] == {"enabled": True, "model_async_compact_threshold_percent": 50}


def test_async_blocked_versions() -> None:
    R = codex_agent.CodexReader
    assert codex_agent.async_blocked([]) == [R("cli")]
    assert codex_agent.async_blocked([R("cli", "0.163.0-alpha.8"), R("app", "0.163.0-alpha.10.2"), R("cli", "0.164.0")]) == []
    assert codex_agent.async_blocked([R("cli", "0.163.0-alpha.5")]) == [R("cli", "0.163.0-alpha.5")]
    assert codex_agent.async_blocked([R("ide", "")]) == [R("ide", "")]
    assert codex_agent.async_blocked([R("cli", "0.162.1")])


def test_extension_readers(tmp_path) -> None:
    root = tmp_path / ".vscode" / "extensions"
    copy = root / "openai.chatgpt-26.51111-darwin-arm64" / "bin" / "macos-aarch64"
    copy.mkdir(parents=True)
    (copy / "codex-package.json").write_text('{"name": "codex", "version": "0.169.0-alpha.11.1"}')
    (root / "openai.chatgpt-1.0.0").mkdir()
    (root / ".obsolete").write_text('{"openai.chatgpt-1.0.0": true}')
    (root / "other.ext-1.0").mkdir()
    assert codex_agent._extension_readers(tmp_path) == [codex_agent.CodexReader("ide", "0.169.0-alpha.11.1")]


# Keys that hold Codex's compaction (codex_compact_held_test.go)

def test_compact_held_named_and_taken_out(homes) -> None:
    config = homes / "codex" / "config.toml"
    config.write_text('# mine\nmodel_context_window = 128_000\nprofile = "work"\nmodel = "x"\n\n'
                      '[profiles.work]\nmodel_auto_compact_token_limit = 100000\nmodel = "y"\n')
    held = codex_agent.compact_held()
    assert [(h.key, h.profile, h.value) for h in held] == [
        ("model_context_window", "", "128000"), ("model_auto_compact_token_limit", "work", "100000")]
    codex_agent.drop_compact_holds()
    assert codex_agent.compact_held() == []
    text = config.read_text()
    assert text.startswith("# mine\n") and _toml(config) == {"profile": "work", "model": "x", "profiles": {"work": {"model": "y"}}}


def test_compact_held_none(homes) -> None:
    assert codex_agent.compact_held() == []
    (homes / "codex" / "config.toml").write_text('model = "x"\n')
    assert codex_agent.compact_held() == []


def test_codex_model_reads_the_profile(homes) -> None:
    config = homes / "codex" / "config.toml"
    config.write_text('model = "gpt-5.5"\nprofile = "p"\n\n[profiles.p]\nmodel_provider = "openprogram"\nmodel = "deepseek/deepseek-chat"\n')
    assert codex_agent.codex_model() == "deepseek/deepseek-chat"
    config.write_text('model = "gpt-5.5"\n')
    assert codex_agent.codex_model() == ""


# The Codex app's error page (codex_app_crash_test.go)

def _log(root, day, text, mod):
    folder = root / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "codex-desktop-581d5b6a-0.log"
    path.write_text(text)
    os.utime(path, (mod, mod))
    return path


def _line(at, what):
    stamp = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if what == "fatal":
        return f"{stamp} error [AppServerConnection] fatal_error_broadcasted initialized=true listenerCount=2\n"
    return f"{stamp} error [AppServerConnection] Codex CLI process exited classifiedAsExpected=true code=0\n"


def test_app_crashed_reads_its_own_logs(tmp_path, monkeypatch) -> None:
    from openprogram.models.gateway import codex_app
    root = tmp_path / "Logs" / "com.openai.codex"
    monkeypatch.setattr(codex_app, "log_roots", lambda: [root])
    since = datetime(2026, 11, 5, 23, 59, 58, tzinfo=timezone.utc).timestamp()
    assert codex_app.app_crashed(since) == (None, True)  # the app never ran
    day = datetime.fromtimestamp(since - 7200, timezone.utc)
    path = _log(root, day, _line(since - 3600, "fatal") + _line(since + 1, "exit"), since + 1)
    assert codex_app.app_crashed(since)[0] is None  # the clean exit and an old page
    half = _line(since + 3, "fatal")
    path.write_text(_line(since + 1, "exit") + half[:-1])
    os.utime(path, (since + 3, since + 3))
    assert codex_app.app_crashed(since)[0] is None  # a line still being written
    path.write_text(_line(since + 1, "exit") + half)
    os.utime(path, (since + 3, since + 3))
    assert codex_app.app_crashed(since) == (since + 3, True)
    os.utime(path, (since - 60, since - 60))
    assert codex_app.app_crashed(since)[0] is None  # a file untouched since the end
    nxt = datetime.fromtimestamp(since + 3600, timezone.utc)
    _log(root, nxt, _line(since + 1, "exit") + _line(since + 4, "fatal"), since + 5)
    assert codex_app.app_crashed(since)[0] == since + 4


def test_page_reopens_the_app_unless_a_turn_ran(tmp_path, monkeypatch) -> None:
    from openprogram.models.gateway import codex_app
    monkeypatch.setattr(codex_app.sys, "platform", "darwin")
    since = time.time()
    monkeypatch.setattr(codex_app, "app_crashed", lambda s: (since + 2, True))
    reopened = []
    monkeypatch.setattr(codex_app, "relaunch_app", lambda: reopened.append(1) or "")
    assert codex_app.watch_crash(tmp_path, since, wait=0) and reopened == [1]
    rollout = tmp_path / "sessions" / "2026" / "rollout.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("{}")
    os.utime(rollout, (since + 10, since + 10))
    assert not codex_app.watch_crash(tmp_path, since, wait=0) and reopened == [1]
    monkeypatch.setattr(codex_app, "app_crashed", lambda s: (None, True))
    assert not codex_app.watch_crash(tmp_path, since, wait=0)


# No gateway key in an agent's files

def test_codex_files_hold_a_key_naming_codex(homes) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import agent_of_key, gateway_key
    engine.connect("codex", {"model": "deepseek/deepseek-chat"})
    table = _toml(homes / "codex" / "config.toml")["model_providers"]["openprogram"]
    assert agent_of_key(table["experimental_bearer_token"]) == "codex"
    engine.disconnect("codex")
    engine.connect("codex", {"model": "deepseek/deepseek-chat", "scope": "profile"})
    for path in (homes / "codex").rglob("*"):
        if path.is_file():
            assert gateway_key() not in path.read_text(errors="ignore"), path


def test_codex_connected_with_the_gateway_key_gets_its_own_on_the_next_sync(homes, monkeypatch) -> None:
    """A connection made while agents were given the gateway key is written
    again with the agent's own on its next sync, nothing asked of the user."""
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import agent_of_key, gateway_key
    real = engine.agent_key
    monkeypatch.setattr(engine, "agent_key", lambda agent: gateway_key())
    engine.connect("codex", {"model": "deepseek/deepseek-chat"})
    monkeypatch.setattr(engine, "agent_key", real)
    config = homes / "codex" / "config.toml"
    assert _toml(config)["model_providers"]["openprogram"]["experimental_bearer_token"] == gateway_key()
    assert engine.owned("codex") and engine.sync("codex")
    assert agent_of_key(_toml(config)["model_providers"]["openprogram"]["experimental_bearer_token"]) == "codex"
    assert engine.owned("codex") and not engine.sync("codex")
