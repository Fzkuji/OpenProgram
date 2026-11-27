"""The agents in Pi's format, after yetone/magpie's tests of them
(``pi_test.go``, ``pi_scope_test.go``, ``pi_thinking_test.go``,
``mothx_test.go``, ``pencil_test.go``, ``stepcode_test.go``, ``omo_test.go``,
``primeagent_test.go``, ``mu_test.go``, ``aside_test.go``,
``liveagent_test.go``, ``omp_test.go``, ``omp_scope_test.go``,
``omp_profile_test.go``; MIT License, Copyright (c) yetone)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

GW = "http://127.0.0.1:18100/gateway"
MODELS = [
    {"id": "deepseek/deepseek-chat", "name": "DS", "provider": "deepseek", "api": "openai-completions",
     "context_window": 128000, "max_tokens": 8192, "thinking_levels": ["low", "high"], "reasoning": True},
    {"id": "openai/gpt-5.5", "name": "GPT", "provider": "openai", "api": "openai-responses", "vision": True,
     "context_window": 400000, "max_tokens": 128000, "thinking_levels": ["none", "low", "medium", "high", "xhigh"]},
    {"id": "anthropic/claude-opus-4-6", "name": "Opus", "provider": "anthropic", "api": "anthropic-messages",
     "context_window": 200000, "max_tokens": 64000, "thinking_levels": ["low", "medium", "high", "max"]},
    {"id": "google/gemini-3-pro", "name": "Gemini", "provider": "google", "api": "google-generative-ai",
     "thinking_levels": ["low", "high", "max"]},
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for name in ("PI_CODING_AGENT_DIR", "OMO_CODING_AGENT_DIR", "SENPI_CODING_AGENT_DIR", "PRIME_AGENT_CODING_AGENT_DIR",
                 "MU_AGENT_DIR", "KYRN_AGENT_DIR", "MU_CODING_AGENT_DIR", "KYRN_CODING_AGENT_DIR", "STEP_CODING_AGENT_DIR",
                 "STEPCODE_CONFIG_DIR", "MOTHX_DIR", "LIVEAGENT_HOME", "K_BRAIN_HOME", "PI_CONFIG_DIR", "OMP_PROFILE",
                 "PI_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "home").mkdir()
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway.agents import running
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    monkeypatch.setattr(running, "any_running", lambda *patterns: False)
    from openprogram.models.gateway.agents import omp
    monkeypatch.setattr(omp, "installed_version", lambda: "")
    return tmp_path / "home"


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2))


def _json(path: Path):
    from openprogram.models.gateway.edit import load
    return load("jsonc", path.read_text())


# The shared format

def test_registry_rows_and_user_agents(home) -> None:
    from openprogram.models.gateway.agents import agent_of_ua, all_agents, find
    ids = [a.id for a in all_agents()]
    for agent_id in ("pi", "omo", "prime-agent", "mu", "stepcode", "aside", "pencil", "liveagent", "mothx", "omp"):
        assert agent_id in ids
    assert find("oh-my-pi").id == "omp" and find("k-brain").id == "liveagent" and find("omo-ai").id == "omo"
    assert agent_of_ua("omo/1.2") == "omo" and agent_of_ua("oh-my-pi/16") == "omp"
    assert agent_of_ua("Vibecoding Client/1.3.104") == "mothx" and agent_of_ua("stepcode/0.4") == "stepcode"


def test_pi_takes() -> None:
    from openprogram.models.gateway.agents.pi_like import takes
    cases = [("xai/grok-4.6", "xai/grok-4.6", True), ("XAI/Grok-4.6", "xai/grok-4.6", True),
             ("xai/grok-4.6:high", "xai/grok-4.6", True), ("xai/*", "xai/grok-4.6", True),
             ("*grok*", "xai/grok-4.6", True),
             ("openprogram/*", "openprogram/deepseek/deepseek-v4", False),  # Pi's * stops at a /
             ("openprogram/*/*", "openprogram/deepseek/deepseek-v4", True),
             ("deepseek/*:low", "openprogram/deepseek/deepseek-v4", True),  # the model id fits
             ("grok-4.6", "xai/grok-4.6", False), ("xai/grok-4.6", "xai/grok-4.7", False)]
    for pattern, ref, want in cases:
        assert takes(pattern, ref) is want, (pattern, ref)


def test_pi_thinking_levels() -> None:
    from openprogram.models.gateway.agents.pi_like import thinking_levels
    assert thinking_levels([], False) is None
    # every level named: one the model lacks is null, so Pi hides it (#243)
    assert thinking_levels(["max"], False) == {"off": None, "minimal": None, "low": None, "medium": None,
                                                "high": None, "xhigh": None, "max": "max"}
    # off is the model's none; a Claude on Messages has off left for Pi to offer
    assert thinking_levels(["none", "high"], False)["off"] == "none"
    assert "off" not in thinking_levels(["high"], True)


def test_pi_wiring_each_model_on_its_own_api(home) -> None:
    from openprogram.models.gateway.agents import engine
    folder = home / ".pi" / "agent"
    _write(folder / "settings.json", '{\n  // the user\'s own\n  "enabledModels": ["xai/grok-4.6:high", "deepseek/*"],\n'
                                     '  "defaultProvider": "xai", "defaultModel": "grok-4.6", "theme": "dark"\n}\n')
    _write(folder / "models.json", {"providers": {"xai": {"apiKey": "MINE"},
                                                  "openprogram": {"compat": {"sendSessionAffinityHeaders": False},
                                                                  "headers": {"X-Mine": "1"}}}})
    row = engine.connect("pi", {"model": "openai/gpt-5.5"})
    assert row["config"]["applied"] and not row["config"]["drift"]
    settings = _json(folder / "settings.json")
    assert settings["defaultProvider"] == "openprogram" and settings["defaultModel"] == "openai/gpt-5.5"
    assert settings["enabledModels"] == ["xai/grok-4.6:high", "deepseek/*", "openprogram/openai/gpt-5.5"]
    assert "// the user's own" in (folder / "settings.json").read_text()
    block = _json(folder / "models.json")["providers"]["openprogram"]
    assert block["baseUrl"] == GW + "/openai/v1" and block["api"] == "openai-completions"
    assert block["apiKey"].startswith("opgw-") and block["headers"] == {"X-Mine": "1"}
    # a sendSessionAffinityHeaders already there stays as it is
    assert block["compat"] == {"sendSessionAffinityHeaders": False}
    models = {m["id"]: m for m in block["models"]}
    assert "api" not in models["deepseek/deepseek-chat"] and models["deepseek/deepseek-chat"]["maxTokens"] == 8192
    assert models["openai/gpt-5.5"]["api"] == "openai-responses"
    assert models["openai/gpt-5.5"]["compat"] == {"supportsStrictMode": True}
    assert models["openai/gpt-5.5"]["promptCache"] == {"short": 300}
    assert models["openai/gpt-5.5"]["thinkingLevelMap"]["off"] == "none"
    opus = models["anthropic/claude-opus-4-6"]
    assert opus["api"] == "anthropic-messages" and opus["baseUrl"] == GW + "/anthropic"
    assert opus["compat"] == {"forceAdaptiveThinking": True} and opus["promptCache"] == {"short": 300, "long": 3600}
    # Gemini's API is left out of Pi's folder
    assert "api" not in models["google/gemini-3-pro"]
    engine.disconnect("pi")
    settings = _json(folder / "settings.json")
    assert settings == {"enabledModels": ["xai/grok-4.6:high", "deepseek/*"], "defaultProvider": "xai",
                        "defaultModel": "grok-4.6", "theme": "dark"}
    assert _json(folder / "models.json")["providers"]["openprogram"] == {
        "compat": {"sendSessionAffinityHeaders": False}, "headers": {"X-Mine": "1"}}


def test_pi_no_list_none_made_and_files_go_again(home) -> None:
    from openprogram.models.gateway.agents import engine
    folder = home / ".pi" / "agent"
    folder.mkdir(parents=True)
    engine.connect("pi", {"model": "deepseek/deepseek-chat"})
    assert "enabledModels" not in _json(folder / "settings.json")
    engine.disconnect("pi")
    assert not (folder / "settings.json").exists() and not (folder / "models.json").exists()


def test_pi_folders(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import mu, omo, pi, prime_agent
    assert pi.folder() == home / ".pi" / "agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~/elsewhere")
    assert pi.folder() == home / "elsewhere"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "relative")  # its working directory's, not taken
    assert pi.folder() == home / ".pi" / "agent"
    assert omo.folder() == home / ".omo" / "agent"
    monkeypatch.setenv("SENPI_CODING_AGENT_DIR", str(home / "senpi"))
    assert omo.folder() == home / "senpi"
    assert prime_agent.folder() == home / ".prime" / "agent"
    monkeypatch.setenv("PRIME_AGENT_CODING_AGENT_DIR", "~/pa")
    assert prime_agent.folder() == home / "pa"
    assert mu.folder() == home / ".mu" / "agent"
    (home / ".kyrn").mkdir()
    assert mu.folder() == home / ".kyrn" / "agent"
    (home / ".mu").mkdir()
    assert mu.folder() == home / ".mu" / "agent"


def test_mu_not_maildir_utils(tmp_path) -> None:
    from openprogram.models.gateway.agents.mu import not_mu_agent
    compiled = tmp_path / "mu"
    compiled.write_bytes(b"\x7fELF maildir")
    assert not_mu_agent(str(compiled))
    shim = tmp_path / "shim"
    shim.write_text("#!/bin/sh\nexec node /x/node_modules/mu-agent/kyrn/bin/mu.mjs \"$@\"\n")
    assert not not_mu_agent(str(shim))


def test_named_keys(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import agent_of_key
    for agent_id, path in (("prime-agent", home / ".prime" / "agent" / "models.json"),
                           ("mu", home / ".mu" / "agent" / "models.json")):
        path.parent.mkdir(parents=True)
        engine.connect(agent_id, {"model": "deepseek/deepseek-chat"})
        assert agent_of_key(_json(path)["providers"]["openprogram"]["apiKey"]) == agent_id
    engine.connect("omo", {"model": "google/gemini-3-pro"})
    omo_models = _json(home / ".omo" / "agent" / "models.json")["providers"]["openprogram"]["models"]
    # OmO's folder keeps Gemini's API, at the gateway's /v1beta, with Gemini's four levels only
    gemini = next(m for m in omo_models if m["id"] == "google/gemini-3-pro")
    assert gemini["api"] == "google-generative-ai" and gemini["baseUrl"] == GW + "/gemini/v1beta"
    assert gemini["thinkingLevelMap"]["max"] is None and gemini["thinkingLevelMap"]["high"] == "high"


# StepCode

def test_stepcode_lifecycle(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    folder = home / ".stepcode"
    _write(folder / "config.toml", '# mine\ndefaultProvider = "stepfun"\ndefaultModel = "step-3"\n'
                                   'enabledModels = ["stepfun/step-3"]\n')
    assert find("stepcode").installed()
    engine.connect("stepcode", {"model": "deepseek/deepseek-chat"})
    from openprogram.models.gateway.edit import load
    config = load("toml", (folder / "config.toml").read_text())
    assert config["defaultProvider"] == "openprogram" and config["defaultModel"] == "deepseek/deepseek-chat"
    assert config["enabledModels"] == ["stepfun/step-3", "openprogram/deepseek/deepseek-chat"]
    block = _json(folder / "models.json")["providers"]["openprogram"]
    assert block["api"] == "openai-completions" and "compat" not in block
    assert all(m.get("api") != "google-generative-ai" for m in block["models"])
    engine.disconnect("stepcode")
    assert (folder / "config.toml").read_text().startswith("# mine\n")
    assert load("toml", (folder / "config.toml").read_text()) == {
        "defaultProvider": "stepfun", "defaultModel": "step-3", "enabledModels": ["stepfun/step-3"]}
    assert not (folder / "models.json").exists()


# MothX

def test_mothx(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    path = home / ".mothx" / "settings.json"
    user = {"defaultProvider": "deepseek-openai", "defaultModel": "deepseek-v4-flash", "theme": "dark",
            "providers": {"mine": {"apiKey": "MINE_KEY", "models": [{"id": "m1"}]},
                          "openprogram": {"thinkingFormat": "qwen", "httpProxy": "http://p"}}}
    _write(path, user)
    assert find("vibecoding").id == "mothx" and find("mothx").installed()
    engine.connect("mothx", {"model": "openai/gpt-5.5"})
    data = _json(path)
    assert data["defaultProvider"] == "openprogram" and data["defaultModel"] == "openai/gpt-5.5"
    block = data["providers"]["openprogram"]
    assert block["baseUrl"] == GW + "/openai/v1" and block["api"] == "openai-chat"
    # a thinkingFormat already there may be the user's; what else they put there stays
    assert block["thinkingFormat"] == "qwen" and block["httpProxy"] == "http://p"
    gpt = next(m for m in block["models"] if m["id"] == "openai/gpt-5.5")
    assert gpt == {"id": "openai/gpt-5.5", "name": "GPT", "reasoning": True, "input": ["text", "image"],
                   "contextWindow": 400000, "maxTokens": 128000}
    assert data["providers"]["mine"]["apiKey"] == "MINE_KEY"
    engine.disconnect("mothx")
    assert _json(path) == user


# Pencil

def test_pencil_models(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    path = home / ".pencil" / "models.json"
    _write(path, {"providers": {"mine": {"api": "anthropic-messages"}}})
    assert find("pen.dev").id == "pencil" and find("pencil").installed()
    engine.connect("pencil", {"model": "deepseek/deepseek-chat"})
    block = _json(path)["providers"]["openprogram"]
    assert block["api"] == "openai-completions" and block["baseUrl"] == GW + "/openai/v1"
    for model in block["models"]:
        # every model on the provider's api, with every field Pencil writes for one
        assert "api" not in model and "baseUrl" not in model
        assert {"input", "contextWindow", "maxTokens", "cost", "compat"} <= set(model)
    gemini = next(m for m in block["models"] if m["id"] == "google/gemini-3-pro")
    assert gemini["contextWindow"] == 128000 and gemini["maxTokens"] == 16384 and gemini["input"] == ["text"]
    engine.disconnect("pencil")
    assert _json(path) == {"providers": {"mine": {"api": "anthropic-messages"}}}


# Aside

def test_aside_writes_the_active_account(home) -> None:
    from openprogram.models.gateway.agents import aside, engine
    _write(home / ".aside" / "accounts.json", {"currentAccountId": 1, "accounts": [{"id": 0}, {"id": 1}]})
    settings = home / ".aside" / "u" / "1" / "settings.json"
    before = {"defaultModel": {"provider": "google", "modelId": "gemini-3", "thinkingLevel": "high"},
              "modelCategories": {"fast": {"provider": "google", "modelId": "flash"}}}
    _write(settings, before)
    assert aside.folder() == home / ".aside" / "u" / "1"
    engine.connect("aside", {"model": "deepseek/deepseek-chat", "deep": "anthropic/claude-opus-4-6"})
    data = _json(settings)
    assert data["defaultModel"] == {"provider": "openprogram", "modelId": "deepseek/deepseek-chat", "thinkingLevel": "high"}
    assert data["modelCategories"]["deep"] == {"provider": "openprogram", "modelId": "anthropic/claude-opus-4-6",
                                               "thinkingLevel": "medium"}
    assert data["modelCategories"]["fast"] == before["modelCategories"]["fast"]
    from openprogram.models.gateway.keys import agent_of_key
    models = _json(settings.parent / "models.json")["providers"]["openprogram"]
    assert agent_of_key(models["apiKey"]) == "aside"
    engine.disconnect("aside")
    assert _json(settings) == before
    # an account whose folder isn't there is passed over
    _write(home / ".aside" / "accounts.json", {"currentAccountId": 7})
    assert aside.folder() == home / ".aside" / "u" / "0"


# LiveAgent

def test_liveagent_providers(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".liveagent" / "config.json"
    _write(path, '// k-brain configuration\n{"defaultModel": "model1", "defaultProvider": "demo",\n'
                 ' "providers": {"demo": {"name": "demo", "baseUrl": "", "models": [{"id": "model1"}]},\n'
                 '  "openprogram": {"name": "Mine", "type": "codex", "requestFormat": "openai-responses",\n'
                 '   "activeModels": ["google/gemini-3-pro"], "models": [{"id": "deepseek/deepseek-chat", "displayName": "Mine DS"}]}},}\n')
    engine.connect("liveagent", {"model": "openai/gpt-5.5"})
    data = _json(path)
    chat, responses, messages = (data["providers"][k] for k in ("openprogram", "openprogram-responses", "openprogram-anthropic"))
    # the user's name, request format, display name and turned-off models stay
    assert chat["name"] == "Mine" and chat["api"] == chat["requestFormat"] == "openai-responses"
    ds = next(m for m in chat["models"] if m["id"] == "deepseek/deepseek-chat")
    assert ds["displayName"] == "Mine DS" and ds["contextWindow"] == 128000
    assert chat["activeModels"] == ["google/gemini-3-pro"]
    assert responses["type"] == "codex" and responses["baseUrl"] == GW + "/openai/v1"
    assert [m["id"] for m in responses["models"]] == ["openai/gpt-5.5"]
    assert messages["type"] == "claude_code" and messages["baseUrl"] == GW + "/anthropic"
    assert "requestFormat" not in messages
    # a default still K-brain's own demo model1 becomes the pick
    assert data["defaultModel"] == "openai/gpt-5.5" and data["defaultProvider"] == "openprogram-responses"
    assert path.read_text().startswith("// k-brain configuration")
    engine.disconnect("liveagent")
    data = _json(path)
    assert data["defaultModel"] == "model1" and set(data["providers"]) == {"demo", "openprogram"}


def test_liveagent_leaves_alone(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, running
    path = home / ".liveagent" / "config.json"
    _write(path, '{"models": []}')
    with pytest.raises(engine.AgentConfigError):
        engine.connect("liveagent", {"model": "openai/gpt-5.5"})
    _write(path, '{}')
    monkeypatch.setattr(running, "any_running", lambda *patterns: True)
    with pytest.raises(engine.AgentConfigError, match="quit LiveAgent"):
        engine.connect("liveagent", {"model": "openai/gpt-5.5"})
    assert path.read_text() == "{}"


def test_liveagent_dir(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import liveagent
    assert liveagent.folder() == home / ".liveagent"
    _write(home / ".liveagent" / "data-dir.json", {"path": str(home / "moved")})
    assert liveagent.folder() == home / "moved"
    monkeypatch.setenv("K_BRAIN_HOME", str(home / "kb"))
    assert liveagent.folder() == home / "kb"


# omp

def test_omp_roles_scope_and_provider(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.edit import load
    folder = home / ".omp" / "agent"
    config = "# mine\nmodelRoles:\n  default: xai/grok-4.6\n  slow: xai/grok-4.6:high\nenabledModels:\n  - xai/grok-4.6\n  - openprogram/deepseek/old\n"
    _write(folder / "config.yml", config)
    _write(folder / "models.json", {"providers": {"mine": {"baseUrl": "http://x"}}})
    engine.connect("omp", {"model": "deepseek/deepseek-chat", "slow": "anthropic/claude-opus-4-6",
                           "advisor": "openai/gpt-5.5"})
    data = load("yaml", (folder / "config.yml").read_text())
    assert data["modelRoles"] == {"default": "openprogram/deepseek/deepseek-chat",
                                  "slow": "openprogram/anthropic/claude-opus-4-6:high",
                                  "advisor": "openprogram/openai/gpt-5.5"}
    # OpenProgram's models one by one give way to all of them
    assert data["enabledModels"] == ["xai/grok-4.6", "openprogram/**"]
    assert data["advisor"] == {"enabled": True}
    models = load("yaml", (folder / "models.yml").read_text())
    assert models["providers"]["mine"] == {"baseUrl": "http://x"}  # carried over from models.json
    block = models["providers"]["openprogram"]
    assert block["baseUrl"] == GW + "/openai/v1" and block["apiKey"].startswith("opgw-")
    assert block["headers"] == {"User-Agent": "omp"} and "auth" not in block
    by_id = {m["id"]: m for m in block["models"]}
    # a version not known stops at xhigh: max is offered as xhigh
    assert by_id["anthropic/claude-opus-4-6"]["thinking"] == {"mode": "anthropic-adaptive",
                                                             "efforts": ["low", "medium", "high", "xhigh"]}
    assert by_id["google/gemini-3-pro"]["api"] == "google-generative-ai"
    assert by_id["google/gemini-3-pro"]["thinking"]["mode"] == "google-level"
    assert by_id["openai/gpt-5.5"]["api"] == "openai-responses"
    engine.disconnect("omp")
    # with its provider gone, an entry naming one of OpenProgram's models
    # would name nothing: it goes too (magpie ompScopeWithout)
    assert (folder / "config.yml").read_text() == config.replace("  - openprogram/deepseek/old\n", "")


def test_omp_versions(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import omp
    from openprogram.models.gateway.agents.engine import wiring
    at = wiring(omp.agent(), {"model": "deepseek/deepseek-chat"})
    new = omp.provider_entry(at, "18.4.4")
    assert "headers" not in new and new["compat"] == {"promptCacheSessionHeader": "x-session-affinity"}
    assert "max" in next(m for m in new["models"] if m["id"] == "anthropic/claude-opus-4-6")["thinking"]["efforts"]
    assert omp.provider_entry(at, "18.1.5")["headers"] == {"User-Agent": "omp/18.1.5"}
    assert "compat" not in omp.provider_entry(at, "16.0.5")


def test_omp_profiles(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import all_agents, omp
    for name in ("work", "default", "Bad", "con", "ok."):
        (home / ".omp" / "profiles" / name / "agent").mkdir(parents=True)
    ids = [a.id for a in all_agents() if a.id.startswith("omp")]
    assert ids == ["omp", "omp#work"]
    monkeypatch.setenv("OMP_PROFILE", "work")
    assert omp.folder() == (home / ".omp" / "profiles" / "work" / "agent", False)
    assert [a.id for a in omp.extra_agents()] == []
    monkeypatch.delenv("OMP_PROFILE")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home / ".pi" / "agent"))
    assert omp.folder() == (home / ".pi" / "agent", True)
    # Pi's folder is no sign of omp
    (home / ".pi" / "agent").mkdir(parents=True)
    assert not omp.agent().installed() or omp.find_tool("omp")


def test_omp_level_split() -> None:
    from openprogram.models.gateway.agents.omp import split_level
    assert split_level("a/b:high") == ("a/b", ":high")
    assert split_level("ollama/qwen3:8b") == ("ollama/qwen3:8b", "")
    assert split_level("a/b,c/d:high") == ("a/b,c/d:high", "")
