"""Kimi Code, AtomCode and Mister Morph on the gateway, after yetone/magpie's
``kimi_test.go``, ``kimi_subagents_test.go``, ``kimi_names_test.go``,
``kimi_efforts_test.go``, ``atomcode_test.go`` and ``morph_test.go`` (MIT):
connecting writes the agent's own provider and model entries, a catalog
change rewrites them, and disconnecting puts back what the user had."""
from __future__ import annotations

import tomllib

import pytest
import yaml

from openprogram.models.gateway import edit

MODELS = [
    {"id": "deepseek/pro", "name": "DeepSeek Pro", "provider": "deepseek", "context_window": 128000,
     "max_tokens": 384000, "vision": False, "reasoning": True, "thinking_levels": ["low", "medium", "high"],
     "default_thinking_level": "medium"},
    {"id": "deepseek/flash", "name": "deepseek/flash", "provider": "deepseek", "context_window": None,
     "max_tokens": None, "vision": True, "reasoning": False, "thinking_levels": []},
]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("KIMI_CODE_HOME", "KIMI_SHARE_DIR", "ATOMCODE_HOME", "MISTER_MORPH_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "home").mkdir()
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import atomcode, kimi, morph
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    catalog = [dict(m) for m in MODELS]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: catalog)
    monkeypatch.setattr(agents, "all_agents", lambda: [kimi.agent(), atomcode.agent(), morph.agent()])
    return tmp_path / "home", catalog


def _toml(path):
    return tomllib.loads(path.read_text())


# Kimi Code

KIMI_SIGNED_IN = '''default_model = "kimi-code/kimi-for-coding"
default_thinking = true

[models."kimi-code/kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
max_context_size = 262144

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
api_key = ""

[providers."managed:kimi-code".oauth]
storage = "file"
key = "oauth/kimi-code"

[loop_control]
max_steps_per_turn = 100
'''


def _kimi_valid(doc):
    """What Kimi's own check wants: the default among the models, each model's provider among the providers."""
    assert doc.get("default_model", "") == "" or doc["default_model"] in doc["models"]
    for model in doc.get("models", {}).values():
        assert model["provider"] in doc["providers"]


def test_kimi_old_cli(state) -> None:
    from openprogram.models.gateway.agents import engine, kimi
    from openprogram.models.gateway.keys import token_for
    home, catalog = state
    path = home / ".kimi" / "config.toml"
    path.parent.mkdir()
    path.write_text(KIMI_SIGNED_IN)
    assert kimi.kimi_dir() == (home / ".kimi", True) and kimi.config_path() == path

    engine.connect("kimi", {"model": "deepseek/pro"})
    doc = _toml(path)
    _kimi_valid(doc)
    provider = doc["providers"]["openprogram"]
    assert provider == {"type": "kimi", "base_url": "http://127.0.0.1:18100/gateway/openai/v1", "api_key": token_for("kimi")}
    pro = doc["models"]["openprogram/deepseek/pro"]
    # kimi-cli has no word for tool calling, a reply cap or effort levels
    assert pro == {"provider": "openprogram", "model": "deepseek/pro", "max_context_size": 128000,
                   "display_name": "DeepSeek Pro", "capabilities": ["thinking"]}
    assert doc["models"]["openprogram/deepseek/flash"] == {
        "provider": "openprogram", "model": "deepseek/flash", "max_context_size": 128000, "capabilities": ["image_in"]}
    assert doc["default_model"] == "openprogram/deepseek/pro" and doc["default_thinking"] is True
    assert doc["providers"]["managed:kimi-code"]["oauth"]["key"] == "oauth/kimi-code"
    assert doc["loop_control"] == {"max_steps_per_turn": 100}
    assert engine.status("kimi")["config"]["drift"] == []

    # a provider added later reaches Kimi's picker; one gone leaves it
    catalog.append({"id": "glm/glm-5", "name": "GLM-5", "context_window": 200000})
    catalog.remove(next(m for m in catalog if m["id"] == "deepseek/flash"))
    assert engine.sync("kimi")
    doc = _toml(path)
    _kimi_valid(doc)
    assert "openprogram/glm/glm-5" in doc["models"] and "openprogram/deepseek/flash" not in doc["models"]

    engine.disconnect("kimi")
    assert path.read_text() == KIMI_SIGNED_IN


def test_kimi_code_keeps_the_users_settings(state) -> None:
    from openprogram.models.gateway.agents import engine
    home, _ = state
    path = home / ".kimi-code" / "config.toml"
    path.parent.mkdir()
    path.write_text('default_model = "x"\n\n[models.x]\nprovider = "p"\nmodel = "m"\n\n[providers.p]\ntype = "kimi"\n')
    engine.connect("kimi", {"model": "deepseek/pro"})
    doc = _toml(path)
    pro = doc["models"]["openprogram/deepseek/pro"]
    # the new Kimi Code: tool calling, a reply cap within the window, effort levels
    assert pro["capabilities"] == ["thinking", "tool_use"] and pro["max_output_size"] == 128000
    assert pro["support_efforts"] == ["low", "medium", "high"] and pro["default_effort"] == "medium"
    assert doc["models"]["openprogram/deepseek/flash"]["capabilities"] == ["image_in", "tool_use"]

    # the user's own keys in OpenProgram's tables stay through syncs
    text = path.read_text().replace("[providers.openprogram]\n",
                                    '[providers.openprogram]\ncustom_headers = { X-Mine = "1" }\n', 1)
    text = text.replace('display_name = "DeepSeek Pro"', 'display_name = "Mine"')
    text = text.replace('default_effort = "medium"', 'default_effort = "low"')
    path.write_text(text)
    for _ in range(3):
        engine.connect("kimi", {"model": "deepseek/pro"})
    doc = _toml(path)
    assert doc["providers"]["openprogram"]["custom_headers"] == {"X-Mine": "1"}
    assert doc["providers"]["openprogram"]["type"] == "kimi"
    assert doc["models"]["openprogram/deepseek/pro"]["display_name"] == "Mine"
    assert doc["models"]["openprogram/deepseek/pro"]["default_effort"] == "low"

    engine.disconnect("kimi")
    doc = _toml(path)
    assert doc == {"default_model": "x", "models": {"x": {"provider": "p", "model": "m"}},
                   "providers": {"p": {"type": "kimi"}}}


def test_kimi_subagent_pool(state) -> None:
    from openprogram.models.gateway.agents import engine
    home, catalog = state
    path = home / ".kimi-code" / "config.toml"
    path.parent.mkdir()
    original = ('default_model = "x"\n\n[models.x]\nprovider = "p"\nmodel = "m"\n\n[providers.p]\ntype = "kimi"\n\n'
                '[secondary_model]\ndefault_model = "x"\n\n[secondary_model.models]\nx = "the user\'s"\n')
    path.write_text(original)
    engine.connect("kimi", {"model": "deepseek/pro", "subagent_model": "deepseek/flash"})
    doc = _toml(path)
    assert doc["secondary_model"]["default_model"] == "openprogram/deepseek/flash"
    assert doc["secondary_model"]["models"] == {"x": "the user's", "openprogram/deepseek/flash": ""}

    # a model leaving the catalog leaves the pool first: Kimi refuses a pool naming a model it doesn't have
    catalog.remove(next(m for m in catalog if m["id"] == "deepseek/flash"))
    engine.connect("kimi", {"model": "deepseek/pro"})
    doc = _toml(path)
    assert "openprogram/deepseek/flash" not in doc["secondary_model"]["models"]
    assert "openprogram/deepseek/flash" not in doc["models"]

    engine.disconnect("kimi")
    assert _toml(path) == tomllib.loads(original)


def test_kimi_no_config(state) -> None:
    from openprogram.models.gateway.agents import engine
    home, _ = state
    path = home / ".kimi-code" / "config.toml"
    engine.connect("kimi", {"model": "deepseek/pro"})
    _kimi_valid(_toml(path))
    engine.disconnect("kimi")
    assert not path.exists()


def test_kimi_thinking_pool_effort_and_telemetry(state) -> None:
    from openprogram.models.gateway.agents import engine, kimi
    home, _ = state
    path = home / ".kimi-code" / "config.toml"
    path.parent.mkdir()
    original = ('default_model = "x"\n\n[models.x]\nprovider = "p"\nmodel = "m"\n\n[providers.p]\ntype = "kimi"\n\n'
                '[thinking]\nenabled = true\neffort = "high"\n')
    path.write_text(original)
    values = {"model": "deepseek/pro"}
    assert kimi.thinking_choices(values) == ["off", "low", "medium", "high"]
    assert kimi.thinking_choices({"model": "deepseek/flash"}) == []
    # the pool is picked several at once, among OpenProgram's models
    pool_field = next(f for f in engine.status("kimi")["fields"] if f["key"] == "subagent_pool")
    assert pool_field["multi"] and "openprogram/deepseek/flash" in [o["value"] for o in pool_field["options"]]
    engine.connect("kimi", {**values, "effort": "off", "subagent_pool": "openprogram/deepseek/pro\nopenprogram/deepseek/flash",
                            "subagent_effort": "high"})
    doc = _toml(path)
    # off keeps the level for the next on, as Kimi's own off does
    assert doc["thinking"] == {"enabled": False, "effort": "high"}
    pool = doc["secondary_model"]
    assert set(pool["models"]) == {"openprogram/deepseek/pro", "openprogram/deepseek/flash"}
    assert pool["default_model"] == "openprogram/deepseek/pro"
    # flash doesn't think, so the pool takes off alone and the effort is left out
    assert kimi.pool_levels(list(pool["models"]), [dict(m) for m in MODELS]) == ["off"]
    assert "default_effort" not in pool
    engine.connect("kimi", {**values, "effort": "medium", "subagent_pool": "openprogram/deepseek/pro", "subagent_effort": "high"})
    doc = _toml(path)
    assert doc["thinking"] == {"enabled": True, "effort": "medium"}
    assert doc["secondary_model"]["models"] == {"openprogram/deepseek/pro": ""}
    assert doc["secondary_model"]["default_effort"] == "high"
    # "" is Kimi's own again: only what OpenProgram wrote goes
    engine.connect("kimi", {**values, "effort": "", "subagent_pool": "openprogram/deepseek/pro"})
    assert "default_effort" not in _toml(path)["secondary_model"]

    engine.set_kept("kimi", {"telemetry": "off"})
    assert _toml(path)["telemetry"] is False
    engine.set_kept("kimi", {"telemetry": ""})
    assert "telemetry" not in _toml(path)
    engine.disconnect("kimi")
    assert _toml(path) == tomllib.loads(original)


# AtomCode

def test_atomcode_effort(state) -> None:
    from openprogram.models.gateway.agents import atomcode, engine
    home, _ = state
    path = home / ".atomcode" / "config.toml"
    assert atomcode.effort_choices({"model": "deepseek/pro"}) == ["low", "medium", "high"]
    assert atomcode.effort_choices({"model": "deepseek/flash"}) == []
    engine.connect("atomcode", {"model": "deepseek/pro", "effort": "high"})
    assert _toml(path)["models"]["openprogram/deepseek/pro"]["reasoning_effort"] == "high"
    # a level the model doesn't take is never written
    engine.connect("atomcode", {"model": "deepseek/pro", "effort": "max"})
    assert _toml(path)["models"]["openprogram/deepseek/pro"]["reasoning_effort"] == "high"
    engine.connect("atomcode", {"model": "deepseek/pro", "effort": ""})
    assert "reasoning_effort" not in _toml(path)["models"]["openprogram/deepseek/pro"]
    engine.disconnect("atomcode")
    assert not path.exists()


def test_atomcode_wiring_and_restore(state) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    home, catalog = state
    path = home / ".atomcode" / "config.toml"
    path.parent.mkdir()
    original = ('default_provider = "mine"\ndefault_model = "mine"\n\n[provider_accounts.acct]\nprovider = "openai"\n\n'
                '[models.mine]\naccount = "acct"\nmodel = "gpt-x"\n')
    path.write_text(original)
    engine.connect("atomcode", {"model": "deepseek/pro"})
    doc = _toml(path)
    assert doc["provider_accounts"]["openprogram"] == {
        "provider": "openai-compatible", "base_url": "http://127.0.0.1:18100/gateway/openai/v1",
        "api_key": token_for("atomcode")}
    assert doc["models"]["openprogram/deepseek/pro"] == {
        "account": "openprogram", "model": "deepseek/pro", "context_window": 128000, "max_tokens": 128000,
        "supports_vision": False, "reasoning_effort_levels": ["low", "medium", "high"]}
    assert doc["models"]["openprogram/deepseek/flash"] == {
        "account": "openprogram", "model": "deepseek/flash", "context_window": 128000, "supports_vision": True}
    assert doc["default_provider"] == doc["default_model"] == "openprogram/deepseek/pro"

    # an effort the user picked stays through a sync
    path.write_text(path.read_text().replace('model = "deepseek/pro"\n', 'model = "deepseek/pro"\nreasoning_effort = "high"\n'))
    catalog.append({"id": "glm/glm-5", "name": "GLM-5"})
    assert engine.sync("atomcode")
    doc = _toml(path)
    assert doc["models"]["openprogram/deepseek/pro"]["reasoning_effort"] == "high"
    assert "openprogram/glm/glm-5" in doc["models"]

    engine.disconnect("atomcode")
    assert _toml(path) == tomllib.loads(original)


def test_atomcode_drops_a_default_it_no_longer_has(state) -> None:
    from openprogram.models.gateway.agents import engine
    home, _ = state
    path = home / ".atomcode" / "config.toml"
    path.parent.mkdir()
    path.write_text('default_model = "gone"\n\n[models.mine]\naccount = "a"\nmodel = "m"\n')
    engine.connect("atomcode", {"model": "deepseek/pro"})
    engine.disconnect("atomcode")
    assert _toml(path) == {"models": {"mine": {"account": "a", "model": "m"}}}


# Mister Morph

def test_morph(state) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    home, _ = state
    path = home / ".morph" / "config.yaml"
    path.parent.mkdir()
    original = "llm:\n  provider: openai\n  model: gpt-5\n  api_key: sk-mine\n  profiles:\n    fast:\n      model: x\n"
    path.write_text(original)
    engine.connect("morph", {"model": "deepseek/pro"})
    llm = yaml.safe_load(path.read_text())["llm"]
    assert llm == {"provider": "openai", "model": "deepseek/pro", "api_key": token_for("morph"),
                   "profiles": {"fast": {"model": "x"}}, "inference_provider": "openai_response_compatible",
                   "endpoint": "http://127.0.0.1:18100/gateway/openai/v1",
                   "headers": {"User-Agent": "mistermorph"}, "context_window_tokens": 128000}
    # a model whose window isn't known takes out the one handed before
    engine.connect("morph", {"model": "deepseek/flash"})
    assert "context_window_tokens" not in yaml.safe_load(path.read_text())["llm"]
    engine.disconnect("morph")
    assert edit.load("yaml", path.read_text()) == yaml.safe_load(original)


def test_morph_effort(state) -> None:
    from openprogram.models.gateway.agents import engine
    home, _ = state
    path = home / ".morph" / "config.yaml"
    engine.connect("morph", {"model": "deepseek/pro", "effort": "xhigh"})
    assert yaml.safe_load(path.read_text())["llm"]["reasoning_effort"] == "xhigh"
    engine.connect("morph", {"model": "deepseek/pro", "effort": ""})
    assert "reasoning_effort" not in yaml.safe_load(path.read_text())["llm"]
    engine.disconnect("morph")
    assert not path.exists()


def test_morph_config_variable(state, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, morph
    home, _ = state
    elsewhere = home / "morph.yaml"
    monkeypatch.setenv("MISTER_MORPH_CONFIG", str(elsewhere))
    assert morph.config_path() == elsewhere
    engine.connect("morph", {"model": "deepseek/pro"})
    assert yaml.safe_load(elsewhere.read_text())["llm"]["model"] == "deepseek/pro"
    engine.disconnect("morph")
    assert not elsewhere.exists()


# The TOML editor's tables named by quoted keys

def test_toml_quoted_tables_are_made_and_removed() -> None:
    text = '[models."kimi"]\nprovider = "kimi"\n'
    out = edit.edit("toml", text, sets={"models.openprogram/a/b\\.1.provider": "openprogram"})
    assert '[models."openprogram/a/b.1"]' in out
    assert tomllib.loads(out)["models"]["openprogram/a/b.1"] == {"provider": "openprogram"}
    assert edit.edit("toml", out, deletes=["models.openprogram/a/b\\.1"]) == text
