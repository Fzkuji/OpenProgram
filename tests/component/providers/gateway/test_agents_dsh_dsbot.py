"""DeepSeek Harness and DS Bot on the gateway, after yetone/magpie's
``internal/agent/dsh_route_test.go``, ``dsh_home_test.go``, ``dsh_creds_test.go``,
``dsh_compat_test.go``, ``dsh_fresh_test.go`` and ``dsbot_test.go``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from openprogram.models.gateway import edit

MODELS = [
    {"id": "deepseek/pro", "name": "pro · DeepSeek", "provider": "deepseek", "context_window": 1000000,
     "max_tokens": 393216, "thinking_levels": ["low", "medium", "high"], "api": "openai-completions"},
    {"id": "deepseek/flash", "name": "flash · DeepSeek", "provider": "deepseek", "api": "openai-completions"},
    {"id": "openai/gpt-5", "name": "GPT-5", "provider": "openai", "vision": True, "api": "openai-responses"},
]

OWN = ("# Your patch layer for this dsh profile.\n"
       "- id: llm-deepseek\n  config:\n    apiKeyEnv: DEEPSEEK_API_KEY\n    thinking: enabled\n"
       "- id: llm-pi-ai\n  name: \"@deepseek-ai/dsh-llm-pi-ai\"\n  config:\n    providers:\n      mine:\n"
       "        displayName: Mine\n        apiKeyEnv: MINE_API_KEY\n        api: openai-completions\n"
       "        baseURL: https://mine.example/v1\n        models:\n          - id: m1\n"
       "- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: deepseek-v4-pro\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DS_BOT_HOME", raising=False)
    monkeypatch.delenv("DS_BOT_SHELL_DATA", raising=False)
    from openprogram.models.gateway import address, agents, conversion
    from openprogram.models.gateway.agents import dsh, ds_bot
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    monkeypatch.setattr(dsh, "desktop_installed", lambda: False)
    monkeypatch.setattr(dsh, "dsh_version", lambda: "")
    monkeypatch.setattr(dsh, "app_version", lambda: "")
    monkeypatch.setattr(ds_bot, "_app", lambda: False)
    monkeypatch.setattr(agents, "all_agents", lambda: [dsh.agent(), ds_bot.agent()])
    agents._install_marks()
    return tmp_path / "dsh"


def _patch(path: Path) -> list:
    return yaml.load(path.read_text(), Loader=yaml.SafeLoader)


def _entry(items: list, row: str) -> dict:
    return next(item for item in items if item["id"] == row)


def test_custom_provider_beside_the_users_own(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True)
    web.write_text(OWN)
    engine.connect("dsh", {"model": "deepseek/pro"})
    text = web.read_text()
    # dsh's own DeepSeek row is left to dsh, the user's route stays
    assert text.startswith("# Your patch layer for this dsh profile.\n- id: llm-deepseek\n  config:\n"
                           "    apiKeyEnv: DEEPSEEK_API_KEY\n    thinking: enabled\n")
    assert text.count("id: llm-pi-ai") == 1 and "# openprogram: " in text
    items = _patch(web)
    providers = _entry(items, "llm-pi-ai")["config"]["providers"]
    assert providers["mine"]["baseURL"] == "https://mine.example/v1"
    chat = providers["openprogram"]
    assert chat["displayName"] == "OpenProgram" and chat["apiKeyEnv"] == "OPENPROGRAM_GATEWAY_KEY"
    assert chat["api"] == "openai-completions" and chat["baseURL"] == "http://127.0.0.1:18100/gateway/openai/v1"
    assert chat["streamIdleTimeoutMs"] == 600000
    assert [m["id"] for m in chat["models"]] == ["deepseek/pro", "deepseek/flash"]
    pro = chat["models"][0]
    # the reply limit is held to what leaves dsh half the window to compact in
    assert pro["contextWindow"] == 1000000 and pro["maxTokens"] == 393216
    assert pro["reasoningEfforts"] == {"low": "low", "medium": "medium", "high": "high"}
    responses = providers["openprogram-responses"]
    assert responses["api"] == "openai-responses" and responses["models"][0]["id"] == "openai/gpt-5"
    assert responses["models"][0]["input"] == ["text", "image"]
    assert responses["models"][0]["compat"] == {"supportsStrictMode": True}
    assert "openprogram-anthropic" not in providers
    assert _entry(items, "agent-default-model")["config"] == {"provider": "openprogram", "model": "deepseek/pro"}
    assert "- id: agent-default-model # openprogram" in text
    # the key: .env, dsh's key store (owner-only, version 1), settings.yaml's routes
    assert edit.load("dotenv", (home / ".env").read_text()) == {"OPENPROGRAM_GATEWAY_KEY": token_for("dsh")}
    creds = home / ".credentials.yaml"
    assert edit.load("yaml", creds.read_text()) == {"version": 1, "refs": {"OPENPROGRAM_GATEWAY_KEY": token_for("dsh")}}
    assert creds.stat().st_mode & 0o077 == 0
    settings = edit.load("yaml", (home / "settings.yaml").read_text())
    assert set(settings["llm-pi-ai"]["providers"]) == {"openprogram", "openprogram-responses"}
    assert engine.status("dsh")["config"]["applied"]

    engine.disconnect("dsh")
    assert web.read_text() == OWN
    assert not (home / ".env").exists() and not creds.exists() and not (home / "settings.yaml").exists()


def test_a_patch_list_goes_back_byte_for_byte_after_a_sync(home, monkeypatch) -> None:
    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import engine
    own = ("# my profile\n- id: llm-pi-ai\n  name: '@deepseek-ai/dsh-llm-pi-ai'\n  config:\n"
           "    # my own relay\n    providers:\n"
           "      myrelay: {api: openai-completions, baseURL: \"https://relay.example/v1\", models: [{id: a}]}\n"
           "- id: agent-default-model\n  config: {provider: myrelay, model: a}  # picked in dsh\n")
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True)
    web.write_text(own)
    engine.connect("dsh", {"model": "deepseek/pro"})
    # a sync and a reconnect write again; the file is still as OpenProgram left it
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS[:2]])
    engine.sync("dsh")
    engine.connect("dsh", {"model": "deepseek/flash"})
    engine.disconnect("dsh")
    assert web.read_text() == own


def test_dsh_saving_the_file_then_sync_and_disconnect(home, monkeypatch) -> None:
    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import engine
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True)
    web.write_text(OWN)
    engine.connect("dsh", {"model": "deepseek/pro"})
    # dsh writes the file anew as it saves an edit, in its own style: a model
    # picked in its composer, a route added beside OpenProgram's, a field on it
    items = _patch(web)
    providers = _entry(items, "llm-pi-ai")["config"]["providers"]
    providers["openprogram"]["reasoning"] = "high"
    providers["openprogram"]["models"][1]["compat"] = {"maxTokensField": "max_tokens"}
    providers["added"] = {"displayName": "Added", "api": "anthropic-messages", "baseURL": "https://added.example"}
    _entry(items, "agent-default-model")["config"] = {"provider": "openprogram", "model": "deepseek/flash",
                                                       "reasoningEffort": "high"}
    web.write_text(yaml.safe_dump(items, sort_keys=False))

    # a catalog sync keeps what dsh added to the route and beside it, and the start
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS[:2]])
    assert engine.sync("dsh")
    items = _patch(web)
    providers = _entry(items, "llm-pi-ai")["config"]["providers"]
    assert "openprogram-responses" not in providers
    assert providers["openprogram"]["reasoning"] == "high" and "added" in providers
    assert providers["openprogram"]["models"][1]["compat"] == {"maxTokensField": "max_tokens"}
    assert _entry(items, "agent-default-model")["config"]["model"] == "deepseek/flash"

    # the file changed since: OpenProgram's route and start come out of it as
    # it is now, and the user's own start goes back
    engine.disconnect("dsh")
    items = _patch(web)
    providers = _entry(items, "llm-pi-ai")["config"]["providers"]
    assert set(providers) == {"mine", "added"}
    assert _entry(items, "agent-default-model")["config"] == {"provider": "deepseek-official",
                                                               "model": "deepseek-v4-pro"}
    assert "openprogram" not in web.read_text()


def test_a_pick_in_dsh_off_the_route_stays_the_users(home) -> None:
    from openprogram.models.gateway.agents import engine
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True)
    web.write_text("[]\n")
    engine.connect("dsh", {"model": "deepseek/pro"})
    # since 0.2 dsh's /model writes the config of OpenProgram's entry, mark and all
    web.write_text(web.read_text().replace("provider: openprogram\n    model: \"deepseek/pro\"",
                                           "provider: deepseek-official\n    model: deepseek-flash"))
    engine.disconnect("dsh")
    items = _patch(web)
    assert items == [{"id": "agent-default-model", "config": {"provider": "deepseek-official",
                                                              "model": "deepseek-flash"}}]
    assert "openprogram" not in web.read_text()


def test_route_keeps_the_api_picked_in_dsh(home) -> None:
    from openprogram.models.gateway.agents import engine
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True)
    web.write_text("[]\n")
    engine.connect("dsh", {"model": "deepseek/pro"})
    web.write_text(web.read_text().replace("api: openai-completions", "api: anthropic-messages"))
    engine.sync("dsh")
    chat = _entry(_patch(web), "llm-pi-ai")["config"]["providers"]["openprogram"]
    assert chat["api"] == "anthropic-messages" and chat["baseURL"] == "http://127.0.0.1:18100/gateway/anthropic"


def test_cli_with_no_profile_yet_and_profile_made_later(home) -> None:
    """A dsh that has booted no profile: the web profile is made as dsh makes
    it; disconnected before dsh boots it, it goes again with its folders."""
    from openprogram.models.gateway.agents import dsh, engine
    home.mkdir()
    web = home / "profiles" / "web" / "cordis.patch.yml"
    engine.connect("dsh", {"model": "deepseek/flash"})
    text = web.read_text()
    assert text.startswith(dsh.PROFILE_TEMPLATE.rsplit("[]", 1)[0])
    assert _entry(_patch(web), "agent-default-model")["config"]["model"] == "deepseek/flash"
    engine.disconnect("dsh")
    assert not (home / "profiles").exists()

    # dsh booted the profile since (package.json beside it): its patch list stays, as dsh's template
    engine.connect("dsh", {"model": "deepseek/flash"})
    (web.parent / "package.json").write_text("{}")
    engine.disconnect("dsh")
    assert web.read_text() == dsh.PROFILE_TEMPLATE

    # a profile dsh makes later is given the route by the sync
    engine.connect("dsh", {"model": "deepseek/flash"})
    acp = home / "profiles" / "acp" / "cordis.patch.yml"
    acp.parent.mkdir()
    acp.write_text(dsh.PROFILE_TEMPLATE)
    engine.sync("dsh")
    assert _entry(_patch(acp), "llm-pi-ai")["config"]["providers"]["openprogram"]["models"]
    engine.disconnect("dsh")
    assert acp.read_text() == dsh.PROFILE_TEMPLATE and web.read_text() == dsh.PROFILE_TEMPLATE


def test_home_patch_layer(home) -> None:
    """The home layer replaces each profile's entry of the same id: an entry it
    has gets what the profiles got; one it hasn't is not added."""
    from openprogram.models.gateway.agents import engine
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True)
    web.write_text("[]\n")
    layer = home / "cordis.patch.yml"
    layer.write_text("- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: deepseek-flash\n")
    engine.connect("dsh", {"model": "deepseek/pro"})
    items = _patch(layer)
    assert [i["id"] for i in items] == ["agent-default-model"]
    assert items[0]["config"] == {"provider": "openprogram", "model": "deepseek/pro"}
    engine.disconnect("dsh")
    assert _patch(layer)[0]["config"] == {"provider": "deepseek-official", "model": "deepseek-flash"}

    layer.write_text("- id: llm-pi-ai\n  config:\n    providers:\n      mine:\n        api: openai-completions\n")
    engine.connect("dsh", {"model": "deepseek/pro"})
    providers = _entry(_patch(layer), "llm-pi-ai")["config"]["providers"]
    assert set(providers) == {"mine", "openprogram", "openprogram-responses"}
    assert "agent-default-model" not in [i["id"] for i in _patch(layer)]
    engine.disconnect("dsh")


def test_key_store_of_another_layout_is_left_alone(home) -> None:
    from openprogram.models.gateway.agents import engine
    home.mkdir()
    creds = home / ".credentials.yaml"
    creds.write_text("version: 2\nkeys: {}\n")
    engine.connect("dsh", {"model": "deepseek/pro"})
    assert creds.read_text() == "version: 2\nkeys: {}\n"
    creds.write_text("version: 1\nrefs:\n  DEEPSEEK_API_KEY: sk-mine\n")
    engine.connect("dsh", {"model": "deepseek/flash"})
    assert edit.load("yaml", creds.read_text())["refs"]["DEEPSEEK_API_KEY"] == "sk-mine"
    engine.disconnect("dsh")
    assert edit.load("yaml", creds.read_text()) == {"version": 1, "refs": {"DEEPSEEK_API_KEY": "sk-mine"}}


def test_a_js_value_is_kept_and_a_list_that_is_not_one_refused(home) -> None:
    from openprogram.models.gateway.agents import engine
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True)
    loop = "- id: agent-loop\n  config:\n    cwd: !!js process.cwd()\n"
    web.write_text(loop)
    engine.connect("dsh", {"model": "deepseek/pro"})
    assert web.read_text().startswith(loop)
    engine.disconnect("dsh")
    assert web.read_text() == loop
    web.write_text("model: x\n")
    with pytest.raises(engine.AgentConfigError):
        engine.connect("dsh", {"model": "deepseek/pro"})
    assert web.read_text() == "model: x\n"


def test_model_limits_and_levels() -> None:
    from openprogram.models.gateway.agents import dsh
    assert dsh.dsh_max_tokens({"context_window": 500000, "max_tokens": 393216}) == 184464
    assert dsh.dsh_max_tokens({"context_window": 131072, "max_tokens": 65536}) == 32768
    assert dsh.dsh_max_tokens({"max_tokens": 8000}) == 8000
    assert dsh.levels({"thinking_levels": ["off"]}) == {}
    assert dsh.levels({"thinking_levels": ["none", "high", "max"]}) == {"off": "none", "high": "high", "max": "max"}


def test_ds_bot_data_folder(home, tmp_path, monkeypatch) -> None:
    from openprogram.models.gateway.agents import ds_bot, engine
    user = tmp_path / "home"
    monkeypatch.setattr(ds_bot, "user_config_dir", lambda: tmp_path / "appdata")
    assert ds_bot.data_dir() == user / ".ds-bot"
    picked = tmp_path / "bot-data"
    loc = tmp_path / "appdata" / "DS Bot" / "locations.json"
    loc.parent.mkdir(parents=True)
    loc.write_text(json.dumps({"dataDir": str(picked), "workspace": "x"}))
    assert ds_bot.data_dir() == picked
    engine.connect("ds_bot", {"model": "deepseek/pro"})
    profile = picked / "profiles" / "ds-bot" / "cordis.patch.yml"
    assert _entry(_patch(profile), "agent-default-model")["config"]["provider"] == "openprogram"
    assert (picked / ".env").is_file() and not home.exists()
    engine.disconnect("ds_bot")
    assert not profile.exists()

    monkeypatch.setenv("DS_BOT_HOME", str(tmp_path / "throwaway"))
    assert ds_bot.data_dir() == tmp_path / "throwaway"
    monkeypatch.delenv("DS_BOT_HOME")
    for bad in ("{", '{"dataDir":""}', '{"workspace":"x"}'):
        loc.write_text(bad)
        assert ds_bot.data_dir() is None
        made = ds_bot.agent()
        assert not made.installed()
        with pytest.raises(engine.AgentConfigError):
            engine.connect("ds_bot", {"model": "deepseek/pro"})
    assert not (user / ".ds-bot").exists()

    # the userData of DS Bot's name before, while DS Bot's has none
    loc.unlink()
    legacy = tmp_path / "appdata" / "DeepSeek Bot"
    legacy.mkdir()
    (legacy / "locations.json").write_text(json.dumps({"dataDir": str(tmp_path / "old")}))
    assert ds_bot.data_dir() == tmp_path / "old"


def test_registry_names(home) -> None:
    from openprogram.models.gateway.agents import agent_of_ua, find
    assert find("deepseek-harness").id == "dsh" and find("dsbot").id == "ds_bot"
    assert agent_of_ua("deepseek-harness/0.2.2") == "dsh"


# Round 2: what magpie's dsh_gemini_test.go, dsh_effort*, dsh_compact_test.go,
# dsh_search_test.go, dsh_creds_test.go, dsh_fresh_test.go and dsh_compat_test.go check

GEMINI = {"id": "google/gemini-3-pro", "name": "Gemini 3 Pro", "provider": "google", "context_window": 1048576,
          "max_tokens": 65536, "thinking_levels": ["low", "high", "xhigh"], "api": "google-generative-ai"}
CLAUDE = {"id": "anthropic/claude-opus-4-6", "name": "Opus", "provider": "anthropic",
          "thinking_levels": ["low", "high", "max"], "api": "anthropic-messages"}


def _web(home: Path, text: str = "[]\n") -> Path:
    web = home / "profiles" / "web" / "cordis.patch.yml"
    web.parent.mkdir(parents=True, exist_ok=True)
    web.write_text(text)
    return web


def _start(path: Path) -> dict:
    return _entry(_patch(path), "agent-default-model")["config"]


def test_gemini_route_waits_for_a_dsh_that_speaks_it(home, monkeypatch) -> None:
    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import dsh, engine
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in [*MODELS, GEMINI, CLAUDE]])
    web = _web(home)
    engine.connect("dsh", {"model": "google/gemini-3-pro"})
    providers = _entry(_patch(web), "llm-pi-ai")["config"]["providers"]
    # no dsh here says a version: the Gemini goes on Chat, as the gateway translates it
    assert "openprogram-gemini" not in providers
    assert "google/gemini-3-pro" in [m["id"] for m in providers["openprogram"]["models"]]
    assert _start(web)["provider"] == "openprogram"
    # an Opus 4.6 on Messages is asked adaptively
    opus = providers["openprogram-anthropic"]["models"][0]
    assert opus["compat"] == {"forceAdaptiveThinking": True}

    monkeypatch.setattr(dsh, "dsh_version", lambda: "0.2.2-rc.5")
    monkeypatch.setattr(dsh, "app_version", lambda: "0.2.2-rc.1")
    assert not dsh.speaks_gemini()  # the desktop app is older
    monkeypatch.setattr(dsh, "app_version", lambda: "")
    assert dsh.speaks_gemini()
    assert engine.sync("dsh")
    providers = _entry(_patch(web), "llm-pi-ai")["config"]["providers"]
    route = providers["openprogram-gemini"]
    assert route["api"] == "google-generative-ai"
    assert route["baseURL"] == "http://127.0.0.1:18100/gateway/gemini/v1beta"
    # xhigh is refused by pi-ai's Google adapter: not offered
    assert route["models"][0]["reasoningEfforts"] == {"low": "low", "high": "high"}
    assert "google/gemini-3-pro" not in [m["id"] for m in providers["openprogram"]["models"]]
    assert _start(web)["provider"] == "openprogram-gemini"
    engine.disconnect("dsh")
    assert web.read_text() == "[]\n"


def test_versions() -> None:
    from openprogram.models.gateway.agents import dsh
    assert dsh.newer("0.2.2-rc.5", "0.2.2-rc.2") and dsh.newer("0.2.2", "0.2.2-rc.9")
    assert not dsh.newer("0.2.2-rc.1", "0.2.2-rc.2") and dsh.newer("0.2.2-rc.10", "0.2.2-rc.9")
    assert dsh.newer("0.0.1-rc.1", "0.0.0") and not dsh.newer("0.0.1-rc.1", "0.1.5")
    assert dsh.levels({"thinking_levels": ["off", "max"]}, "google-generative-ai") == {}
    assert dsh.asks_adaptively("claude-sonnet-4-6") and not dsh.asks_adaptively("claude-sonnet-4-20250514")
    assert dsh.asks_adaptively("glm-5.3") and dsh.asks_adaptively("gemini-3-pro")


def test_thinking_effort(home) -> None:
    from openprogram.models.gateway.agents import engine
    web = _web(home)
    # a choice among the picked model's levels: none before a model is picked
    assert not any(f["key"] == "effort" for f in engine.status("dsh")["fields"])
    engine.connect("dsh", {"model": "deepseek/pro", "effort": "high"})
    assert _start(web) == {"provider": "openprogram", "model": "deepseek/pro", "reasoningEffort": "high"}
    choices = [o["value"] for o in next(f for f in engine.status("dsh")["fields"] if f["key"] == "effort")["options"]]
    assert choices == ["low", "medium", "high"]
    # one the model doesn't take is refused; flash takes none
    with pytest.raises(engine.AgentConfigError, match="takes an effort of low, medium, high"):
        engine.connect("dsh", {"model": "deepseek/pro", "effort": "max"})
    with pytest.raises(engine.AgentConfigError, match="no thinking levels"):
        engine.connect("dsh", {"model": "deepseek/flash", "effort": "low"})
    # a start of the user's in dsh keeps its model; dsh's own default takes the effort out again
    items = _patch(web)
    _entry(items, "agent-default-model")["config"] = {"provider": "openprogram", "model": "openai/gpt-5"}
    web.write_text(yaml.safe_dump(items, sort_keys=False))
    engine.connect("dsh", {"model": "deepseek/pro", "effort": "low"})
    assert _start(web) == {"provider": "openprogram-responses", "model": "openai/gpt-5", "reasoningEffort": "low"}
    engine.connect("dsh", {"model": "deepseek/pro"})
    assert "reasoningEffort" not in _start(web)
    engine.disconnect("dsh")


class SimpleModel:
    def __init__(self, provider: str, id_: str) -> None:
        self.provider, self.id = provider, id_


def test_compaction_model(home, monkeypatch) -> None:
    from openprogram.models.gateway import conversion, dsh_compact
    from openprogram.models.gateway.agents import engine
    from openprogram.models.providers.types import Context, TextContent, UserMessage
    web = _web(home)
    asked = SimpleModel("deepseek", "pro")
    moved = SimpleModel("deepseek", "flash")
    monkeypatch.setattr(conversion, "resolve_model", lambda ref: moved)
    compaction = Context(messages=[UserMessage(content=[TextContent(text=dsh_compact.ASK + "\n...")], timestamp=0)])
    turn = Context(messages=[UserMessage(content="hello", timestamp=0)])
    assert dsh_compact.compact_to("dsh", {}, compaction, asked) is asked  # none picked
    engine.connect("dsh", {"model": "deepseek/pro", "compact_model": "openprogram/deepseek/flash"})
    assert dsh_compact.compact_to("dsh", {}, compaction, asked) is moved
    assert dsh_compact.compact_to("dsh", {"x-deepseek-harness-compact": "1"}, turn, asked) is moved
    assert dsh_compact.compact_to("dsh", {"x-deepseek-harness-compact": "false"}, turn, asked) is asked
    assert dsh_compact.compact_to("dsh", {}, turn, asked) is asked
    assert dsh_compact.compact_to("ds_bot", {}, compaction, asked) is asked
    # it writes nothing into dsh's files; DS Bot has no field of its own
    assert "flash" not in json.dumps(_start(web))
    assert "compact_model" not in [f["key"] for f in engine.status("ds_bot")["fields"]]
    engine.connect("dsh", {"model": "deepseek/pro", "compact_model": "openai/gone"})
    assert dsh_compact.compact_to("dsh", {}, compaction, asked) is asked  # not served: stays
    engine.disconnect("dsh")


def test_a_reply_limit_typed_in_dsh_stays(home) -> None:
    from openprogram.models.gateway.agents import engine
    web = _web(home)
    engine.connect("dsh", {"model": "deepseek/pro"})
    items = _patch(web)
    _entry(items, "llm-pi-ai")["config"]["providers"]["openprogram"]["models"][0]["maxTokens"] = 20000
    web.write_text(yaml.safe_dump(items, sort_keys=False))
    engine.sync("dsh")
    pro = _entry(_patch(web), "llm-pi-ai")["config"]["providers"]["openprogram"]["models"][0]
    assert pro["maxTokens"] == 20000
    engine.disconnect("dsh")


def test_imported_settings_kept_and_key_store_owner_only(home) -> None:
    from openprogram.models.gateway.agents import engine
    _web(home)
    (home / "settings.yaml.imported").write_text("llm-deepseek:\n  thinking: enabled\n")
    creds = home / ".credentials.yaml"
    creds.write_text("version: 1\nrefs:\n  DEEPSEEK_API_KEY: sk-mine\n")
    creds.chmod(0o644)
    engine.connect("dsh", {"model": "deepseek/pro"})
    kept = home / "settings.yaml.imported.kept"
    assert kept.read_text() == "llm-deepseek:\n  thinking: enabled\n"
    assert kept.stat().st_mode & 0o077 == 0 and creds.stat().st_mode & 0o077 == 0
    assert (home / "settings.yaml").stat().st_mode & 0o077 == 0
    engine.disconnect("dsh")
    assert kept.exists()


def test_a_dsh_before_profiles(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import dsh, engine
    from openprogram.models.gateway.keys import token_for
    home.mkdir()
    monkeypatch.setattr(dsh, "dsh_version", lambda: "0.0.0")
    engine.connect("dsh", {"model": "deepseek/pro", "effort": "max"})
    config = home / "config.yaml"
    items = yaml.load(config.read_text(), Loader=dsh._Loader)  # noqa: S506
    llm = _entry(items, "llm-deepseek")["config"]
    assert llm["apiKey"] == token_for("dsh") and llm["baseURL"] == "http://127.0.0.1:18100/gateway/openai/v1"
    assert llm["reasoningEffort"] == "max" and llm["models"][0]["id"] == "deepseek/pro"
    assert _entry(items, "agent-loop")["config"]["agents"][0]["model"] == "deepseek/pro"
    assert _entry(items, "api-gateway")["config"] == {"provider": "deepseek-official", "model": "deepseek/pro"}
    assert not (home / "profiles").exists()
    choices = [o["value"] for o in next(f for f in engine.status("dsh")["fields"] if f["key"] == "effort")["options"]]
    assert choices == ["off", "low", "high", "max"]
    engine.disconnect("dsh")
    assert not config.exists()

    # a version not known: by an entry of the user's in config.yaml
    monkeypatch.setattr(dsh, "dsh_version", lambda: "")
    config.write_text("- id: agent-loop\n  config:\n    agents: []\n")
    assert dsh.before_profiles(home)
    config.unlink()
    assert not dsh.before_profiles(home)


def test_web_search_through_the_gateway(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import dsh, engine
    web = _web(home)
    engine.connect("dsh", {"model": "deepseek/pro"})
    items = _patch(web)
    search = _entry(items, dsh.SEARCH_ROW)
    assert search["name"] == dsh.SEARCH_PLUGIN
    assert search["config"] == {"apiKeyEnv": "OPENPROGRAM_GATEWAY_KEY", "model": "deepseek/pro",
                                "baseURL": "http://127.0.0.1:18100/gateway/anthropic/v1"}
    assert _entry(items, dsh.WEB_ROW)["config"] == {"fetchProvider": "http", "searchProvider": "deepseek-official"}
    # "Automatic" in dsh takes the web row out: a sync doesn't put it back
    head, rows = dsh.parse(web.read_text())
    web.write_text(dsh.join(head, [row for row in rows if row.id != dsh.WEB_ROW]))
    engine.sync("dsh")
    assert dsh.WEB_ROW not in [i["id"] for i in _patch(web)]
    engine.disconnect("dsh")
    assert dsh.SEARCH_ROW not in web.read_text()

    # a DeepSeek key of dsh's own: it searches as it does
    web.write_text("[]\n")
    (home / ".env").write_text("DEEPSEEK_API_KEY=sk-mine\n")
    engine.connect("dsh", {"model": "deepseek/pro"})
    assert dsh.SEARCH_ROW not in [i["id"] for i in _patch(web)]
    engine.disconnect("dsh")


def test_found_by_its_desktop_app_alone(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import base, ds_bot, dsh
    monkeypatch.setattr(base, "find_tool", lambda name: "")
    assert not dsh.agent().installed()
    monkeypatch.setattr(dsh, "desktop_installed", lambda: True)
    assert dsh.agent().installed()
    monkeypatch.setattr(ds_bot, "_app", lambda: True)
    assert ds_bot.agent().installed()


def test_gemini_route_requests_are_dshs(home) -> None:
    from openprogram.models.gateway.agents import agent_of_ua
    assert agent_of_ua("google-genai-sdk/2.32.0 gl-node/v25.8.2, deepseek-harness/0.2.2-rc.5 "
                       "(+https://github.com/deepseek-ai/deepseek-harness) dsh-profile/headless") == "dsh"
    assert agent_of_ua("google-genai-sdk/2.32.0 gl-node/v25.8.2 (compatible; deepseek-harness)") is None
    assert agent_of_ua("MyApp/2.0 deepseek-harness/0.2.2") is None


def test_dshs_own_models_as_picks(home, monkeypatch) -> None:
    """dsh's own models, as dsh lists them, are offered beside OpenProgram's
    (magpie's dsh model ``Options``): one picked starts sessions on dsh's own
    provider, and OpenProgram's model list moving never moves it."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from openprogram.interfaces.server.routes.settings import harness_models
    from openprogram.models.gateway.agents import engine, lifecycle
    web = _web(home, OWN)
    own = [{"id": "deepseek-official:deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
           {"id": "openprogram/deepseek/pro", "label": "pro"}]  # never one of OpenProgram's
    monkeypatch.setattr(lifecycle, "OWN_MODELS", {})
    monkeypatch.setattr(lifecycle, "OPTIONS", {})
    app = FastAPI()
    harness_models.register(app)  # hands down the runtime's reading, replaced here
    lifecycle.provide_own_models("dsh", lambda: own)
    model = next(f for f in engine.status("dsh")["fields"] if f["key"] == "model")
    assert model["withCatalog"] and model["options"] == [
        {"value": "deepseek-official:deepseek-v4-flash", "label": "DeepSeek V4 Flash"}]

    client = TestClient(app)
    row = client.put("/api/harnesses/models", json={"harness": "dsh", "model": "deepseek-official:deepseek-v4-flash",
                                                     "tiers": {"effort": "max"}}).json()
    assert row["model"] == "deepseek-official:deepseek-v4-flash"
    assert _start(web) == {"provider": "deepseek-official", "model": "deepseek-v4-flash", "reasoningEffort": "max"}
    # OpenProgram's routes are there for the other pickers
    assert "openprogram" in _entry(_patch(web), "llm-pi-ai")["config"]["providers"]
    assert [o["value"] for o in next(f for f in row["fields"] if f["key"] == "effort")["options"]] == [
        "off", "low", "high", "max"]
    assert client.put("/api/harnesses/models", json={"harness": "dsh", "model": "deepseek-v4-flash"}).status_code == 400
    assert not lifecycle.reseat(["deepseek/flash"])
    assert engine.status("dsh")["values"]["model"] == "deepseek-official:deepseek-v4-flash"
    # a pick the listing has no more stays offered
    own.clear()
    model = next(f for f in engine.status("dsh")["fields"] if f["key"] == "model")
    assert [o["value"] for o in model["options"]] == ["deepseek-official:deepseek-v4-flash"]
    engine.disconnect("dsh")
    assert web.read_text() == OWN
