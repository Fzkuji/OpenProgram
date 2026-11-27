"""Editors and IDEs on the gateway, after yetone/magpie's tests of the same agents
(``vscode_test.go``, ``vscode_detect_test.go``, ``zoocode_test.go``,
``zed_test.go``, ``zed_reasoning_test.go``, ``cursorlocal_test.go``,
``antigravity_test.go``): VS Code and its builds, Zoo Code, Zed, Cursor
Private Inference and Antigravity."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.models.gateway import edit

MODELS = [
    {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "context_window": 128000,
     "max_tokens": 8192, "vision": False, "reasoning": True, "thinking_levels": ["low", "medium", "high"],
     "default_thinking_level": "high"},
    {"id": "openrouter/x", "name": "X", "provider": "openrouter", "context_window": None, "max_tokens": None,
     "vision": True, "reasoning": False, "thinking_levels": [], "default_thinking_level": None},
]
REF = "deepseek/deepseek-chat"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("AGY_LLM_GATEWAY_URL", raising=False)
    from openprogram.models.gateway import address, conversion
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    from openprogram.models.gateway.agents import vscode
    monkeypatch.setattr(vscode, "OS", "darwin")
    monkeypatch.setattr(vscode, "find_tool", lambda name: "")
    return tmp_path


def use(monkeypatch, *built):
    """Make ``built`` the registry the engine finds agents in."""
    from openprogram.models.gateway import agents
    monkeypatch.setattr(agents, "all_agents", lambda: list(built))
    return built[0]


def jsonc(path: Path):
    return edit.load("jsonc", path.read_text())


def items(path: Path):
    from openprogram.models.gateway.edit.jsonc import to_json
    return json.loads(to_json(path.read_text()) or "null")


# VS Code

def _vscode_user(home: Path, folder: str = "Code") -> Path:
    user = home / "Library" / "Application Support" / folder / "User"
    user.mkdir(parents=True)
    return user


def test_vscode_round_trip_with_profiles(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, vscode
    user = _vscode_user(home)
    settings = user / "settings.json"
    settings.write_text('{\n  // mine\n  "chat.defaultModel": "gpt-4o",\n  "editor.fontSize": 13\n}\n')
    models = user / "chatLanguageModels.json"
    models.write_text('[\n  {"vendor": "customendpoint", "name": "mine", "models": []}\n]\n')
    (user / "profiles" / "p1").mkdir(parents=True)
    (user / "profiles" / "p2").mkdir(parents=True)
    # a list that was there empty before connect comes back empty (magpie)
    (user / "profiles" / "p1" / "chatLanguageModels.json").write_text("[]\n")
    (user / "globalStorage").mkdir()
    (user / "globalStorage" / "storage.json").write_text(json.dumps({"userDataProfiles": [
        {"location": "p1", "name": "Work"},
        {"location": "p2", "name": "Shared", "useDefaultFlags": {"settings": True, "languageModels": True}},
        {"location": "gone", "name": "Gone"}]}))
    agent = use(monkeypatch, vscode.agent())
    assert agent.installed() and agent.ua == ("githubcopilotchat", "vscode_agent_host") and agent.named_key

    engine.connect("vscode", {"model": REF})
    text = settings.read_text()
    assert "// mine" in text and jsonc(settings)["chat.defaultModel"] == REF
    group = next(g for g in items(models) if g["name"] == "openprogram")
    assert group["vendor"] == "customendpoint" and group["apiType"] == "chat-completions"
    first = group["models"][0]
    from openprogram.models.gateway.keys import gateway_key, token_for
    assert first["url"] == "http://127.0.0.1:18100/gateway/openai/v1/chat/completions"
    # a key of its own written into its file, never the gateway's
    assert first["requestHeaders"] == {"Authorization": "Bearer " + token_for("vscode")} != {
        "Authorization": "Bearer " + gateway_key()}
    assert first["toolCalling"] and first["contextWindow"] == 128000 and first["maxOutputTokens"] == 8192
    assert first["supportsReasoningEffort"] == ["low", "medium", "high"]
    assert group["models"][1]["contextWindow"] == 128000 and group["models"][1]["maxOutputTokens"] == 8192
    assert any(g["name"] == "mine" for g in items(models))
    p1 = user / "profiles" / "p1"
    assert jsonc(p1 / "settings.json")["chat.defaultModel"] == REF
    assert any(g["name"] == "openprogram" for g in items(p1 / "chatLanguageModels.json"))
    assert not (user / "profiles" / "p2" / "settings.json").exists()
    status = engine.status("vscode")
    assert status["config"]["applied"] and not status["config"]["drift"] and "Reload Window" in status["notice"]

    # VS Code writes the effort picked in Chat's model picker into the group; a sync keeps it
    raw = models.read_text().replace('"apiType": "chat-completions"',
                                     '"apiType": "chat-completions", "settings": {"x": {"reasoningEffort": "high"}}')
    models.write_text(raw)
    engine.sync("vscode")
    assert next(g for g in items(models) if g["name"] == "openprogram")["settings"] == {"x": {"reasoningEffort": "high"}}

    engine.disconnect("vscode")
    assert jsonc(settings) == {"chat.defaultModel": "gpt-4o", "editor.fontSize": 13} and "// mine" in settings.read_text()
    assert [g["name"] for g in items(models)] == ["mine"]
    assert not (p1 / "settings.json").exists()
    assert items(p1 / "chatLanguageModels.json") == [] and (p1 / "chatLanguageModels.json").exists()


def test_vscode_drift_when_the_group_is_gone(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, vscode
    user = _vscode_user(home)
    use(monkeypatch, vscode.agent())
    engine.connect("vscode", {"model": REF})
    (user / "chatLanguageModels.json").write_text("[]")
    assert engine.status("vscode")["config"]["drift"]


def test_vscode_insiders_sends_its_own_key(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, vscode_insiders
    from openprogram.models.gateway.keys import token_for
    user = _vscode_user(home, "Code - Insiders")
    agent = use(monkeypatch, vscode_insiders.agent())
    assert agent.named_key and not agent.ua
    engine.connect("vscode_insiders", {"model": REF})
    group = items(user / "chatLanguageModels.json")[0]
    assert group["models"][0]["requestHeaders"]["Authorization"] == "Bearer " + token_for("vscode_insiders")


def test_vscodium_writes_for_the_copilot_chat_it_has(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, vscodium
    from openprogram.models.gateway.keys import token_for
    user = _vscode_user(home, "VSCodium")
    exts = home / ".vscode-oss" / "extensions"
    chat = exts / "github.copilot-chat-0.48.1"
    chat.mkdir(parents=True)
    (chat / "package.json").write_text(json.dumps({"version": "0.48.1", "contributes": {
        "languageModelChatProviders": [{"vendor": "customoai"}]}}))
    (exts / "github.copilot-chat-0.40.0").mkdir()
    use(monkeypatch, vscodium.agent())
    engine.connect("vscodium", {"model": REF})
    [group] = items(user / "chatLanguageModels.json")
    assert group["vendor"] == "customoai" and "apiType" not in group
    first = group["models"][0]
    assert first["requestHeaders"] == {"x-api-key": token_for("vscodium")}
    assert first["maxInputTokens"] == 128000 - 8192 and "contextWindow" not in first
    assert "personal Copilot plan" in engine.status("vscodium")["notice"]


def test_vscode_chat_found_without_extensions_json(tmp_path) -> None:
    from openprogram.models.gateway.agents.vscode import chat_of, newer
    assert chat_of(tmp_path / "none").none
    (tmp_path / "github.copilot-chat-0.30.0").mkdir()
    (tmp_path / "github.copilot-chat-0.31.2-darwin-arm64").mkdir()
    (tmp_path / "github.copilot-chat-0.31.2-darwin-arm64" / "package.json").write_text(
        json.dumps({"contributes": {"languageModelChatProviders": [{"vendor": "copilot"}]}}))
    found = chat_of(tmp_path)
    assert found.version == "0.31.2" and found.unsupported
    assert newer("1.10", "1.9") and not newer("1.0", "1.0.0")


def test_vscode_launcher_of_a_fork_is_not_vscode(tmp_path) -> None:
    from openprogram.models.gateway.agents.vscode import code_binary, name_short
    app = tmp_path / "Cursor.app" / "Contents" / "Resources" / "app"
    (app / "bin").mkdir(parents=True)
    (app / "bin" / "code").write_text("#!/bin/sh\n")
    (app / "product.json").write_text(json.dumps({"applicationName": "cursor", "nameShort": "Cursor"}))
    assert not code_binary(str(app / "bin" / "code"), "code")
    (app / "product.json").write_text(json.dumps({"nameShort": "Code - OSS"}))
    assert code_binary(str(app / "bin" / "code"), "code")
    assert code_binary(str(tmp_path / "wrapper" / "code"), "code")
    assert name_short("Visual Studio Code") and not name_short("Qoder")


def test_vscode_portable_mac_build(tmp_path, monkeypatch) -> None:
    from openprogram.models.gateway.agents import vscode
    monkeypatch.setattr(vscode, "OS", "darwin")
    bundle = tmp_path / "Code" / "Visual Studio Code.app"
    app = bundle / "Contents" / "Resources" / "app"
    (app / "bin").mkdir(parents=True)
    (app / "bin" / "code").write_text("")
    (app / "product.json").write_text(json.dumps({"applicationName": "code"}))
    (tmp_path / "Code" / "code-portable-data").mkdir()
    monkeypatch.setattr(vscode, "find_tool", lambda name: str(app / "bin" / "code"))
    assert vscode.portable_data(vscode.STABLE) == tmp_path / "Code" / "code-portable-data"
    user, exts = vscode.dirs(vscode.STABLE)
    assert user == tmp_path / "Code" / "code-portable-data" / "user-data" / "User"
    (app / "product.json").write_text(json.dumps({"applicationName": "code", "target": "user"}))
    assert vscode.portable_data(vscode.STABLE) is None


# Zoo Code

def test_zoo_code_imports_a_profile_and_stops(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine, zoo_code
    from openprogram.models.gateway.keys import token_for
    user = _vscode_user(home)
    settings = user / "settings.json"
    settings.write_text('{"zoo-code.autoImportSettingsPath": "/mine.json"}')
    (user / "profiles" / "p1").mkdir(parents=True)
    (user / "globalStorage").mkdir()
    (user / "globalStorage" / "storage.json").write_text(json.dumps({"userDataProfiles": [{"location": "p1"}]}))
    agent = use(monkeypatch, zoo_code.agent())
    assert not agent.installed()
    (home / ".vscode" / "extensions" / "zoocodeorganization.zoo-code-3.90.0").mkdir(parents=True)
    assert agent.installed()

    engine.connect("zoo_code", {"model": REF})
    file = zoo_code.import_path(user)
    assert jsonc(settings)["zoo-code.autoImportSettingsPath"] == str(file)
    assert jsonc(user / "profiles" / "p1" / "settings.json")["zoo-code.autoImportSettingsPath"] == str(file)
    profiles = json.loads(file.read_text())["providerProfiles"]
    profile = profiles["apiConfigs"]["openprogram"]
    assert profiles["currentApiConfigName"] == "openprogram"
    assert profile["openAiBaseUrl"] == "http://127.0.0.1:18100/gateway/openai/v1"
    assert profile["openAiApiKey"] == token_for("zoo_code") and profile["openAiModelId"] == REF
    assert profile["openAiCustomModelInfo"] == {"supportsPromptCache": False, "supportsImages": False,
                                                "contextWindow": 128000, "maxTokens": 8192}
    assert "Reload Window" in engine.status("zoo_code")["notice"]
    assert not engine.status("zoo_code")["config"]["drift"]

    engine.disconnect("zoo_code")
    assert jsonc(settings)["zoo-code.autoImportSettingsPath"] == "/mine.json"
    assert not (user / "profiles" / "p1" / "settings.json").exists()
    assert not file.exists()


# Zed

@pytest.fixture
def saved(monkeypatch):
    from openprogram.models.gateway.agents import zed
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(zed, "save_credential", lambda url, key: calls.append((url, key)))
    return calls


def test_zed_provider_credential_and_restore(home, monkeypatch, saved) -> None:
    from openprogram.models.gateway.agents import engine, zed
    from openprogram.models.gateway.keys import token_for
    path = home / ".config" / "zed" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{\n  // mine\n  "agent": {"default_model": {"provider": "zed.dev", "model": "claude"}},\n'
                    '  "theme": "One Dark"\n}\n')
    use(monkeypatch, zed.agent())
    engine.connect("zed", {"model": REF})
    url = "http://127.0.0.1:18100/gateway/openai/v1"
    assert saved == [(url, token_for("zed"))]
    doc = jsonc(path)
    assert doc["agent"]["default_model"] == {"provider": "openprogram", "model": REF}
    provider = doc["language_models"]["openai_compatible"]["openprogram"]
    assert provider["api_url"] == url
    first, second = provider["available_models"]
    # the window a prompt and its reply share
    assert first["max_tokens"] == 128000 + 8192 and first["max_output_tokens"] == 8192
    assert first["reasoning_effort"] == "high" and first["capabilities"]["tools"]
    assert second["max_tokens"] == 128000 and "reasoning_effort" not in second and second["capabilities"]["images"]
    assert not engine.status("zed")["config"]["drift"]

    # a level the user picked in Zed stays through a sync
    path.write_text(path.read_text().replace('"reasoning_effort": "high"', '"reasoning_effort": "low"'))
    engine.sync("zed")
    assert jsonc(path)["language_models"]["openai_compatible"]["openprogram"]["available_models"][0][
        "reasoning_effort"] == "low"

    engine.disconnect("zed")
    assert jsonc(path) == {"agent": {"default_model": {"provider": "zed.dev", "model": "claude"}}, "theme": "One Dark"}
    assert "// mine" in path.read_text()


def test_zed_block_and_pick_put_back(home, monkeypatch, saved) -> None:
    """OpenProgram's provider is its whole while connected (what the user adds
    inside stays) and goes whole on disconnect; the model picked in Zed is
    left by a sync and put back on disconnect only while it still names
    OpenProgram's provider."""
    from openprogram.models.gateway.agents import engine, zed
    path = home / ".config" / "zed" / "settings.json"
    path.parent.mkdir(parents=True)
    mine = {"provider": "zed.dev", "model": "claude"}
    path.write_text(json.dumps({"agent": {"default_model": mine}}))
    use(monkeypatch, zed.agent())
    engine.connect("zed", {"model": REF})

    doc = jsonc(path)
    doc["language_models"]["openai_compatible"]["openprogram"]["note"] = "mine"
    doc["agent"]["default_model"] = {"provider": "openprogram", "model": "openrouter/x"}
    path.write_text(json.dumps(doc))
    engine.sync("zed")
    doc = jsonc(path)
    assert doc["language_models"]["openai_compatible"]["openprogram"]["note"] == "mine"
    assert doc["agent"]["default_model"] == {"provider": "openprogram", "model": "openrouter/x"}
    engine.disconnect("zed")
    assert jsonc(path) == {"agent": {"default_model": mine}}

    # one picked in Zed off OpenProgram stays the user's
    engine.connect("zed", {"model": REF})
    doc = jsonc(path)
    doc["agent"]["default_model"] = {"provider": "zed.dev", "model": "gpt"}
    path.write_text(json.dumps(doc))
    engine.disconnect("zed")
    assert jsonc(path) == {"agent": {"default_model": {"provider": "zed.dev", "model": "gpt"}}}


def test_zed_credential_failure_leaves_settings(home, monkeypatch) -> None:
    from openprogram.models.gateway import stash
    from openprogram.models.gateway.agents import engine, zed

    def refuse(url, key):
        raise engine.AgentConfigError("configure Zed gateway credential: denied")
    monkeypatch.setattr(zed, "save_credential", refuse)
    path = home / ".config" / "zed" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"theme": "x"}')
    use(monkeypatch, zed.agent())
    with pytest.raises(engine.AgentConfigError, match="credential"):
        engine.connect("zed", {"model": REF})
    assert path.read_text() == '{"theme": "x"}' and stash.entry("zed") is None


def test_zed_through_a_proxy(home, monkeypatch, saved) -> None:
    """magpie zed_proxy_test.go: Zed's provider pointed at the gateway through a
    proxy keeps it while the gateway answers there; a bili prefix stays when
    the gateway's port moves."""
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import engine, reach, zed
    reach.forget()
    answers = {"https://ai.corp.example/op": True, "https://down.example": False}
    monkeypatch.setattr(reach, "probe", lambda base, path, key: answers.get(base, False))
    path = home / ".config" / "zed" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    use(monkeypatch, zed.agent())
    engine.connect("zed", {"model": REF})
    gw = "http://127.0.0.1:18100/gateway/openai/v1"

    def api_url() -> str:
        return jsonc(path)["language_models"]["openai_compatible"]["openprogram"]["api_url"]

    # a company's reverse proxy the user put in front of the gateway stays
    corp = "https://ai.corp.example/op/gateway/openai/v1"
    path.write_text(path.read_text().replace(gw, corp))
    engine.connect("zed", {"model": REF})
    assert api_url() == corp and saved[-1][0] == corp
    assert not engine.status("zed")["config"]["drift"]
    engine.sync("zed")
    assert api_url() == corp

    # a proxy the gateway doesn't answer through is written away
    path.write_text(path.read_text().replace(corp, "https://down.example/gateway/openai/v1"))
    engine.connect("zed", {"model": REF})
    assert api_url() == gw

    # billion-context's lane goes on to the gateway where it is now
    path.write_text(path.read_text().replace(gw, "http://127.0.0.1:8787/bili/" + gw))
    engine.connect("zed", {"model": REF})
    assert api_url() == "http://127.0.0.1:8787/bili/" + gw
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    engine.connect("zed", {"model": REF})
    moved = "http://127.0.0.1:8787/bili/http://127.0.0.1:18200/gateway/openai/v1"
    assert api_url() == moved and saved[-1][0] == moved
    engine.disconnect("zed")
    assert jsonc(path) == {}


def test_reach_proxy_base() -> None:
    from openprogram.models.gateway.agents import reach
    want = "http://127.0.0.1:18100/gateway/openai/v1"
    assert reach.proxy_base("https://ai.corp.example/op/gateway/openai/v1/", want) == ("https://ai.corp.example/op", True)
    assert reach.proxy_base("http://127.0.0.1:8788/route/gateway/openai/v1", want) == ("http://127.0.0.1:8788/route", True)
    # the gateway's own on an older port is never another way to it
    assert reach.proxy_base("http://127.0.0.1:3425/gateway/openai/v1", want) == ("", False)
    assert reach.proxy_base("https://ai.corp.example/elsewhere", want) == ("", False)
    assert reach.chains_to_gateway("http://127.0.0.1:18787/bili/" + want, want)
    assert not reach.chains_to_gateway("http://127.0.0.1:18787/bili/https://api.example.com/v1", want)
    assert reach.bili_to_gateway("http://127.0.0.1:8787/bili/anthropic/http://127.0.0.1:1/x") == \
        "http://127.0.0.1:8787/bili/anthropic/"
    assert reach.bili_to_gateway("http://127.0.0.1:8787/bili/https://api.example.com") == ""


def test_zed_reasoning_effort() -> None:
    from openprogram.models.gateway.agents.zed import effort
    assert effort({"reasoning": False}) == ""
    assert effort({"reasoning": True}) == "high"
    assert effort({"thinking_levels": ["minimal", "low"]}) == "low"
    assert effort({"thinking_levels": ["low", "xhigh"]}) == "high"
    assert effort({"thinking_levels": ["off", "medium"]}) == "medium"


# Cursor Private Inference

def test_cursor_local_found_and_switched(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import cursor_local, engine, userenv
    from openprogram.models.gateway.keys import token_for
    apps = home / "Applications"
    regular = apps / "Cursor.app" / "Contents" / "Resources" / "app"
    regular.mkdir(parents=True)
    (regular / "product.json").write_text(json.dumps({"nameShort": "Cursor", "applicationName": "cursor"}))
    monkeypatch.setattr(cursor_local, "roots", lambda: [apps])
    monkeypatch.setattr(cursor_local, "installs", lambda: [])
    monkeypatch.setitem(cursor_local._seen, "at", 0.0)
    agent = use(monkeypatch, cursor_local.agent())
    assert not agent.installed()
    local = apps / "Cursor Private Inference.app"
    (local / "Contents" / "Resources" / "app").mkdir(parents=True)
    (local / "Contents" / "Resources" / "app" / "product.json").write_text(
        json.dumps({"nameShort": "Cursor Private Inference", "applicationName": "cursor"}))
    (local / "Contents" / "Info.plist").write_text(
        "<plist><dict><key>CFBundleExecutable</key>\n<string>Cursor</string></dict></plist>")
    monkeypatch.setitem(cursor_local._seen, "at", 0.0)
    assert cursor_local.installed_app() == str(local / "Contents" / "MacOS" / "Cursor")
    assert agent.switch and cursor_local.app_image("Cursor_Private_Inference-3.24.9-x86_64.AppImage")
    assert not cursor_local.app_image("Cursor-3.24.9-x86_64.AppImage")

    sets: list = []
    monkeypatch.setattr(userenv, "set_user_env", lambda names, env, file: sets.append(env))
    engine.connect("cursor_local", {})
    base = "http://127.0.0.1:18100/gateway/openai/v1"
    assert sets == [{"CURSOR_LOCAL_AGENT_BASE_URL": base, "CURSOR_LOCAL_AGENT_API_KEY": token_for("cursor_local")}]
    status = engine.status("cursor_local")
    assert status["config"]["applied"] and status["switch"] and not status["config"]["drift"]
    from openprogram.models.gateway.agents import lifecycle
    assert lifecycle.rewire() == [] and len(sets) == 2  # set again as the Worker starts
    launch = cursor_local.launch_command("/A/it's.app", base, "k")
    assert launch.startswith(f"CURSOR_LOCAL_AGENT_BASE_URL={base} ") and "'\\''" in launch
    engine.disconnect("cursor_local")
    assert sets[-1] is None and not cursor_local.mark_path().exists()


def test_user_env_file_on_linux(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import userenv
    monkeypatch.setattr(userenv.sys, "platform", "linux")
    userenv.set_user_env(["A", "B"], {"A": "1", "B": "2"}, file="t")
    path = home / ".config" / "environment.d" / "t.conf"
    assert path.read_text() == "A=1\nB=2\n"
    userenv.set_user_env(["A", "B"], None, file="t")
    assert not path.exists()


def test_key_files_are_private_from_the_start(home, monkeypatch) -> None:
    """The environment.d file and Cursor's marker are made 0600 as they are
    created, not chmodded after; the marker holds no key."""
    import os
    from openprogram.models.gateway.agents import cursor_local, userenv
    monkeypatch.setattr(userenv.sys, "platform", "linux")
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)  # only the mode a file is made with counts
    old = os.umask(0)
    try:
        userenv.set_user_env(["A", "B"], {"A": "1", "B": "secret"}, file="t")
        monkeypatch.setattr(userenv, "set_user_env", lambda names, env, file: None)
        cursor_local._on({"CURSOR_LOCAL_AGENT_BASE_URL": "http://x/v1", "CURSOR_LOCAL_AGENT_API_KEY": "secret"})
    finally:
        os.umask(old)
    path = home / ".config" / "environment.d" / "t.conf"
    assert path.read_text() == "A=1\nB=secret\n" and (path.stat().st_mode & 0o777) == 0o600
    mark = cursor_local.mark_path()
    assert (mark.stat().st_mode & 0o777) == 0o600 and "secret" not in mark.read_text()


# Antigravity

def test_antigravity_both_files_and_back(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import antigravity, engine
    from openprogram.models.gateway.keys import token_for
    legacy = home / ".antigravity" / "settings.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"gateway": {"enabled": True, "url": "https://corp", "apiKey": "mine"},
                                  "models": {"defaultModelId": "gemini-3", "list": [{"modelId": "gemini-3"}]},
                                  "other": 1}))
    monkeypatch.setattr(antigravity, "app_version", lambda: "")
    agent = use(monkeypatch, antigravity.agent())
    assert not agent.installed()
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / ".gemini" / "antigravity" / "agyhub_summaries_proto.pb").write_text("")
    assert agent.installed()

    engine.connect("antigravity", {"model": REF})
    config = home / ".gemini" / "config" / "config.json"
    for path in (config, legacy):
        doc = json.loads(path.read_text())
        assert doc["gateway"] == {"enabled": True, "url": "http://127.0.0.1:18100/gateway/openai",
                                  "apiKey": token_for("antigravity"), "wireProtocol": "openai"}
        # the catalog lists DeepSeek once a level; the app starts on medium
        assert doc["models"]["defaultModelId"] == REF + ":medium" and "list" not in doc["models"]
        assert doc["models"]["listSource"] == "http://127.0.0.1:18100/gateway/openai/v1/models?format=antigravity"
    assert antigravity.start_model() == REF + ":medium"
    # a level the user picked stays while the catalog lists it
    assert antigravity.start_on(REF, REF + ":high") == REF + ":high"
    assert antigravity.start_on(REF, REF + ":max") == REF + ":medium"
    assert antigravity.start_on("openrouter/x", REF + ":high") == "openrouter/x"
    assert "restart Antigravity" in engine.status("antigravity")["notice"]
    assert not engine.status("antigravity")["config"]["drift"]

    monkeypatch.setenv("AGY_LLM_GATEWAY_URL", "https://elsewhere")
    assert "AGY_LLM_GATEWAY_URL" in engine.status("antigravity")["config"]["error"]
    monkeypatch.delenv("AGY_LLM_GATEWAY_URL")

    engine.disconnect("antigravity")
    assert json.loads(legacy.read_text()) == {"gateway": {"enabled": True, "url": "https://corp", "apiKey": "mine"},
                                              "models": {"defaultModelId": "gemini-3",
                                                         "list": [{"modelId": "gemini-3"}]}, "other": 1}
    assert not config.exists()


def _antigravity(home, monkeypatch):
    from openprogram.models.gateway.agents import antigravity
    monkeypatch.setattr(antigravity, "app_version", lambda: "")
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / ".gemini" / "antigravity" / "agyhub_summaries_proto.pb").write_text("")
    config = home / ".gemini" / "config" / "config.json"
    config.parent.mkdir(parents=True)
    return use(monkeypatch, antigravity.agent()), config


def test_antigravity_block_and_pick_put_back(home, monkeypatch) -> None:
    """The gateway block is OpenProgram's whole; the model picked in the app
    goes back on disconnect while the gateway still carries OpenProgram's
    key, and stays when the user moved the gateway off it."""
    from openprogram.models.gateway.agents import engine
    _, config = _antigravity(home, monkeypatch)
    before = {"gateway": {"enabled": False, "url": "https://corp"}, "models": {"defaultModelId": "gemini-3"}}
    config.write_text(json.dumps(before))
    engine.connect("antigravity", {"model": REF})
    doc = json.loads(config.read_text())
    doc["gateway"]["timeout"] = 30
    doc["models"]["defaultModelId"] = "openrouter/x"
    config.write_text(json.dumps(doc))
    engine.sync("antigravity")
    doc = json.loads(config.read_text())
    assert doc["gateway"]["timeout"] == 30 and doc["models"]["defaultModelId"] == "openrouter/x"
    engine.disconnect("antigravity")
    assert json.loads(config.read_text()) == before

    engine.connect("antigravity", {"model": REF})
    doc = json.loads(config.read_text())
    doc["gateway"]["apiKey"] = "theirs"
    doc["models"]["defaultModelId"] = "gemini-x"
    config.write_text(json.dumps(doc))
    engine.disconnect("antigravity")
    assert json.loads(config.read_text())["models"]["defaultModelId"] == "gemini-x"


def test_antigravity_effort_starts_the_model_at_it(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import antigravity, engine
    agent, config = _antigravity(home, monkeypatch)
    effort = next(field for field in agent.fields if field.key == "effort")
    assert effort.options() == []  # offered only while on OpenProgram
    engine.connect("antigravity", {"model": REF})
    assert effort.options() == [("low", "low"), ("medium", "medium"), ("high", "high")]
    engine.connect("antigravity", {"model": REF, "effort": "high"})
    assert antigravity.start_model() == REF + ":high"
    engine.disconnect("antigravity")


def test_antigravity_catalog(home, monkeypatch) -> None:
    from openprogram.models.gateway import models_info
    listed = models_info.antigravity_list()["list"]
    # a model with two or more levels is listed once a level, as the app's picker gathers them
    assert listed[0] == {"modelId": REF + ":low", "displayName": "DeepSeek Chat (Low)", "maxTokens": 128000,
                         "maxOutputTokens": 8192, "supportsThinking": True}
    assert [e["modelId"] for e in listed] == [REF + ":low", REF + ":medium", REF + ":high", "openrouter/x"]
    assert listed[3] == {"modelId": "openrouter/x", "displayName": "X", "supportsImages": True}
    from openprogram.models.gateway import conversion
    many = [{"id": f"p/m{i}", "name": f"M{i}"} for i in range(40)]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: many)
    fitted = models_info.antigravity_list(start="p/m39")["list"]
    assert len(fitted) == 32 and fitted[-1]["modelId"] == "p/m39"


def test_antigravity_levels() -> None:
    from openprogram.models.gateway.models_info import antigravity_levels
    assert antigravity_levels(["high", "low"]) == ["low", "high"]
    assert antigravity_levels(["none", "minimal", "low", "medium", "high", "xhigh", "max"]) == [
        "low", "medium", "high", "xhigh", "max"]
    assert antigravity_levels(["minimal", "high"]) == [] and antigravity_levels(["high"]) == []


def test_antigravity_model_starts_on_a_level(home, monkeypatch) -> None:
    from openprogram.models.gateway import conversion, models_info
    rows = [{"id": "ds/three", "thinking_levels": ["low", "medium", "high"]},
            {"id": "ds/two", "thinking_levels": ["low", "high"]}, {"id": "ds/plain"}]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: rows)
    assert models_info.antigravity_model("ds/three") == "ds/three:medium"
    assert models_info.antigravity_model("ds/two") == "ds/two:low"
    assert models_info.antigravity_model("ds/plain") == "ds/plain"
    assert models_info.antigravity_model("ds/gone") == "ds/gone"


@pytest.mark.parametrize("n", [25, 40])
def test_antigravity_catalog_fits_the_app(home, monkeypatch, n) -> None:
    from openprogram.models.gateway import conversion, models_info
    rows = [{"id": f"ds/m{i:02d}", "thinking_levels": ["low", "medium", "high"]} for i in range(n)]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: rows)
    listed = [e["modelId"] for e in models_info.antigravity_list(start="none/x")["list"]]
    assert len(listed) <= models_info.ANTIGRAVITY_MENU_MAX
    bases: list[str] = []
    for model_id in listed:
        base = model_id.split(":")[0]
        if not bases or bases[-1] != base:
            bases.append(base)
    assert bases == [r["id"] for r in rows[:models_info.ANTIGRAVITY_MENU_MAX]]
    if n < models_info.ANTIGRAVITY_MENU_MAX:
        assert len(listed) >= models_info.ANTIGRAVITY_MENU_MAX - 1
    for base in bases:
        start = models_info.antigravity_model(base)
        assert start in [e["modelId"] for e in models_info.antigravity_list(start=base)["list"]]


@pytest.mark.parametrize("n,at", [(20, 15), (40, 36)])
def test_antigravity_start_model_keeps_its_levels(home, monkeypatch, n, at) -> None:
    from openprogram.models.gateway import conversion, models_info
    neon = ["low", "medium", "high", "xhigh"]
    rows = [{"id": "cpa/gemini-4-neon" if i == at else f"cpa/m{i:02d}",
             "thinking_levels": neon if i == at else ["low", "medium", "high"]} for i in range(n)]
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: rows)
    got = [e["modelId"] for e in models_info.antigravity_list(start="cpa/gemini-4-neon")["list"]]
    assert len(got) <= models_info.ANTIGRAVITY_MENU_MAX
    assert all(f"cpa/gemini-4-neon:{level}" in got for level in neon)
