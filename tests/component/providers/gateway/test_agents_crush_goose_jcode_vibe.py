"""Crush, Goose, jcode and Mistral Vibe on the gateway, after yetone/magpie's
``crush_test.go``, ``goose_test.go``, ``jcode_test.go`` and ``vibe_test.go``
(MIT License, Copyright (c) yetone)."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from openprogram.models.gateway import edit

MODELS = [
    {"id": "kimi/k2", "name": "K2", "provider": "kimi", "context_window": 262144, "max_tokens": 500000,
     "reasoning": True, "thinking_levels": ["low", "high"], "vision": False},
    {"id": "openai/gpt-5.5", "name": "GPT-5.5", "provider": "openai", "vision": True},
]
GATEWAY = "http://127.0.0.1:18100/gateway"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "JCODE_HOME", "VIBE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    from openprogram.models.gateway import address, conversion
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    from openprogram.models.gateway import agents
    from openprogram.models.gateway.agents import crush, goose, jcode, muse, vibe
    monkeypatch.setattr(agents, "all_agents", lambda: [m.agent() for m in (crush, goose, jcode, vibe, muse)])
    return tmp_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# Crush

def test_crush_picks_where_crush_reads_them(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    config = _write(home / ".config" / "crush" / "crush.json", '{"mcp": {"x": {"command": "y"}}}\n')
    data = _write(home / ".local" / "share" / "crush" / "crush.json",
                  '{"models":{"large":{"model":"m-mine","provider":"mine","max_tokens":99}}}\n')
    before_config, before_data = config.read_text(), data.read_text()
    engine.connect("crush", {"model": "kimi/k2"})
    picks = json.loads(data.read_text())["models"]
    # the whole object replaced: Crush's per-model settings don't ride along
    assert picks["large"] == {"provider": "openprogram", "model": "kimi/k2"}
    assert picks["small"] == {"provider": "openprogram", "model": "kimi/k2"}
    provider = json.loads(config.read_text())["providers"]["openprogram"]
    assert provider["type"] == "openai" and provider["base_url"] == GATEWAY + "/openai/v1"
    assert provider["api_key"] == token_for("crush")
    k2, gpt = provider["models"]
    assert k2 == {"id": "kimi/k2", "name": "K2", "context_window": 262144, "default_max_tokens": 262144,
                  "can_reason": True, "supports_attachments": False}
    assert gpt["context_window"] == 200000 and gpt["default_max_tokens"] == 16384 and gpt["supports_attachments"]
    assert json.loads(config.read_text())["mcp"] == {"x": {"command": "y"}}
    engine.disconnect("crush")
    assert json.loads(config.read_text()) == json.loads(before_config)
    assert json.loads(data.read_text()) == json.loads(before_data)


def test_crush_small_pick_and_absent_files(home) -> None:
    from openprogram.models.gateway.agents import crush, engine
    engine.connect("crush", {"model": "kimi/k2", "small": "openai/gpt-5.5"})
    assert json.loads(crush.data_path().read_text())["models"]["small"]["model"] == "openai/gpt-5.5"
    engine.disconnect("crush")
    assert not crush.config_path().exists() and not crush.data_path().exists()


def test_crush_paths_follow_xdg(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import crush
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", "~/.config")  # unexpanded: ignored
    assert crush.data_path() == home / "data" / "crush" / "crush.json"
    assert crush.config_path() == home / ".config" / "crush" / "crush.json"


# Goose

def test_goose_runs_on_the_gateway_old_layout(home) -> None:
    from openprogram.models.gateway.agents import engine, goose
    from openprogram.models.gateway.keys import token_for
    config = _write(home / ".config" / "goose" / "config.yaml",
                    "# mine\nGOOSE_PROVIDER: anthropic\nGOOSE_MODEL: claude-sonnet-4-5\n")
    engine.connect("goose", {"model": "openai/gpt-5.5"})
    assert edit.load("yaml", config.read_text()) == {"GOOSE_PROVIDER": "openprogram", "GOOSE_MODEL": "openai/gpt-5.5"}
    provider = json.loads(goose.provider_path().read_text())
    assert goose.provider_path().parent.parent == config.parent
    assert provider["engine"] == "openai" and provider["base_url"] == GATEWAY + "/openai/v1"
    assert provider["headers"] == {"Authorization": f"Bearer {token_for('goose')}"}
    assert provider["api_key_env"] == "" and provider["requires_auth"] is False
    assert provider["dynamic_models"] is False and provider["skip_canonical_filtering"] is True
    # goose sends a thinking level only to OpenAI's reasoning models
    assert provider["models"] == [{"name": "kimi/k2", "reasoning": False, "context_limit": 262144},
                                  {"name": "openai/gpt-5.5", "reasoning": True}]
    engine.disconnect("goose")
    assert config.read_text().startswith("# mine\n")
    assert edit.load("yaml", config.read_text()) == {"GOOSE_PROVIDER": "anthropic", "GOOSE_MODEL": "claude-sonnet-4-5"}
    assert not goose.provider_path().exists()


def test_goose_model_in_the_new_layout(home) -> None:
    from openprogram.models.gateway.agents import engine
    text = ("GOOSE_MODE: auto\nactive_provider: anthropic\nproviders:\n  anthropic:\n    enabled: true\n"
            "    model: claude-sonnet-4-5\n    configured: true\nGOOSE_PROVIDER: anthropic\n")
    config = _write(home / ".config" / "goose" / "config.yaml", text)
    engine.connect("goose", {"model": "kimi/k2"})
    doc = edit.load("yaml", config.read_text())
    assert doc["active_provider"] == "openprogram" and "GOOSE_PROVIDER" not in doc
    assert doc["providers"]["openprogram"] == {"enabled": True, "model": "kimi/k2", "configured": True}
    assert doc["providers"]["anthropic"]["model"] == "claude-sonnet-4-5"
    engine.disconnect("goose")
    assert edit.load("yaml", config.read_text()) == edit.load("yaml", text)


def test_goose_keeps_the_users_settings(home) -> None:
    from openprogram.models.gateway.agents import engine, goose
    _write(home / ".config" / "goose" / "config.yaml", "GOOSE_MODE: auto\n")
    engine.connect("goose", {"model": "kimi/k2"})
    path = goose.provider_path()
    mine = json.loads(path.read_text())
    mine.update({"timeout_seconds": 600, "display_name": "My gateway", "base_url": "http://elsewhere"})
    mine["headers"]["X-Mine"] = "1"
    path.write_text(json.dumps(mine))
    from openprogram.models.gateway.agents import lifecycle
    engine.sync("goose", options=lifecycle.options_for("goose"))
    kept = json.loads(path.read_text())
    assert kept["timeout_seconds"] == 600 and kept["display_name"] == "My gateway"
    assert kept["headers"]["X-Mine"] == "1" and kept["base_url"] == GATEWAY + "/openai/v1"


def test_goose_detect_skips_go_goose(home) -> None:
    from openprogram.models.gateway.agents import goose
    binary = _write(home / "bin" / "goose", "")
    binary.write_bytes(b"\x7fELF....\xff Go buildinf:....")
    binary.chmod(0o755)
    assert not goose.agent().installed()
    binary.write_bytes(b"#!/bin/sh\necho goose\n")
    assert goose.agent().installed()


# jcode

JCODE = """[provider]
# my own pick
default_provider = "deepseek"
default_model = "deepseek-chat"

[providers.deepseek]
type = "openai-compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "JCODE_PROVIDER_DEEPSEEK_API_KEY"
default_model = "deepseek-chat"
requires_api_key = true

[[providers.deepseek.models]]
id = "deepseek-chat"
"""


def test_jcode(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    path = _write(home / ".jcode" / "config.toml", JCODE)
    engine.connect("jcode", {"model": "kimi/k2"})
    doc = tomllib.loads(path.read_text())
    assert doc["provider"] == {"default_provider": "openprogram", "default_model": "kimi/k2"}
    profile = doc["providers"]["openprogram"]
    assert profile["type"] == "openai-compatible" and profile["base_url"] == GATEWAY + "/openai/v1"
    assert profile["requires_api_key"] is True and profile["api_key"] == token_for("jcode")
    assert profile["default_model"] == "kimi/k2"
    k2, gpt = profile["models"]
    assert k2 == {"id": "kimi/k2", "context_window": 262144, "input": ["text"], "reasoning": True}
    assert gpt == {"id": "openai/gpt-5.5", "context_window": 128000, "input": ["text", "image"]}
    assert JCODE[JCODE.index("[providers.deepseek]"):] in path.read_text() and "# my own pick\n" in path.read_text()

    # switching between the gateway's models keeps what jcode was on before
    engine.connect("jcode", {"model": "openai/gpt-5.5"})
    engine.disconnect("jcode")
    assert path.read_text() == JCODE


def test_jcode_sync_keeps_theirs(home) -> None:
    from openprogram.models.gateway.agents import engine, lifecycle
    path = _write(home / ".jcode" / "config.toml", JCODE)
    engine.connect("jcode", {"model": "kimi/k2"})
    text = path.read_text().replace('{ id = "kimi/k2", context_window = 262144, input = ["text"], reasoning = true }',
                                    '{ id = "kimi/k2", context_window = 262144, input = ["text"], reasoning = true, '
                                    'reasoning_effort = "high" }')
    path.write_text(text + 'headers = { X = "1" }\n')
    MODELS[0]["context_window"] = 131072
    try:
        engine.sync("jcode", options=lifecycle.options_for("jcode"))
    finally:
        MODELS[0]["context_window"] = 262144
    profile = tomllib.loads(path.read_text())["providers"]["openprogram"]
    assert profile["headers"] == {"X": "1"}
    assert profile["models"][0] == {"id": "kimi/k2", "context_window": 131072, "input": ["text"],
                                    "reasoning": True, "reasoning_effort": "high"}


def test_jcode_home(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import jcode
    monkeypatch.setenv("JCODE_HOME", str(home / "elsewhere"))
    assert jcode.config_path() == home / "elsewhere" / "config.toml"


# Mistral Vibe

VIBE = """# my own pick
active_model = "devstral-2"
vim_keybindings = true

[[providers]]
name = "mistral"
api_base = "https://api.mistral.ai/v1"
api_key_env_var = "MISTRAL_API_KEY"
backend = "mistral"

[[models]]
name = "mistral-vibe-cli-latest"
provider = "mistral"
alias = "devstral-2"
"""


def test_vibe(home) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.models.gateway.keys import token_for
    path = _write(home / ".vibe" / "config.toml", VIBE)
    engine.connect("vibe", {"model": "kimi/k2"})
    doc = tomllib.loads(path.read_text())
    assert doc["active_model"] == "openprogram/kimi/k2" and doc["vim_keybindings"] is True
    mistral, ours = doc["providers"]
    assert mistral["name"] == "mistral"
    assert ours == {"name": "openprogram", "api_base": GATEWAY + "/openai/v1", "api_style": "openai",
                    "backend": "generic", "extra_headers": {"Authorization": f"Bearer {token_for('vibe')}"}}
    own, k2, gpt = doc["models"]
    assert own["alias"] == "devstral-2"
    assert k2 == {"name": "kimi/k2", "provider": "openprogram", "alias": "openprogram/kimi/k2",
                  "thinking_levels": ["off", "low", "high"], "supports_images": False, "max_context_length": 262144}
    assert gpt["thinking_levels"] == ["off"] and gpt["supports_images"] and gpt["max_context_length"] == 128000
    engine.connect("vibe", {"model": "openai/gpt-5.5"})
    assert tomllib.loads(path.read_text())["active_model"] == "openprogram/openai/gpt-5.5"
    engine.disconnect("vibe")
    assert path.read_text() == VIBE


def test_vibe_from_nothing(home) -> None:
    from openprogram.models.gateway.agents import engine, vibe
    engine.connect("vibe", {"model": "kimi/k2"})
    assert tomllib.loads(vibe.config_path().read_text())["active_model"] == "openprogram/kimi/k2"
    engine.disconnect("vibe")
    assert not vibe.config_path().exists()


def test_vibe_home_and_not_avibe(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import vibe
    monkeypatch.setenv("VIBE_HOME", "~/vibe-here")
    assert vibe.config_path() == home / "vibe-here" / "config.toml"
    avibe = _write(home / "bin" / "vibe", "#!/bin/sh\n")
    avibe.chmod(0o755)
    real = _write(home / "avibe" / "bin" / "vibe", "#!/bin/sh\n")
    real.chmod(0o755)
    avibe.unlink()
    os.symlink(real, home / "bin" / "vibe")
    monkeypatch.setattr(vibe, "app_bundles", lambda app: [])
    assert vibe.avibe(str(home / "bin" / "vibe")) and not vibe.agent().installed()
    assert not vibe.avibe("/usr/local/bin/vibe")
    # Vibe Desktop alone, not opened yet, is Vibe
    monkeypatch.setattr(vibe, "app_bundles", lambda app: [Path("/Applications/Vibe.app")])
    assert vibe.agent().installed()


def test_vibe_leaves_the_users_blocks_as_written(home) -> None:
    """magpie SetTOMLArrayTables: the user's [[providers]] and [[models]]
    stay where they are with their comments, through a connect, a sync after
    the user edited the file, and a disconnect."""
    from openprogram.models.gateway.agents import engine
    own = VIBE.replace('backend = "mistral"\n', 'backend = "mistral"  # theirs\n\n# my models\n')
    path = _write(home / ".vibe" / "config.toml", own)
    engine.connect("vibe", {"model": "kimi/k2"})
    text = path.read_text()
    users = own[own.index("[[providers]]"):own.index("[[models]]")]
    assert users in text and text.index(users) < text.index('name = "openprogram"')
    assert '[[models]]\nname = "mistral-vibe-cli-latest"\nprovider = "mistral"\nalias = "devstral-2"\n' in text
    path.write_text(text + "\n# after\n[[models]]\nname = \"cs\"\nprovider = \"mistral\"\nalias = \"cs\"\n")
    engine.connect("vibe", {"model": "openai/gpt-5.5"})
    assert "# after\n[[models]]\nname = \"cs\"" in path.read_text()
    engine.disconnect("vibe")
    text = path.read_text()
    assert "openprogram" not in text and text.startswith(own[:own.index("active_model")])
    assert "backend = \"mistral\"  # theirs\n\n# my models\n" in text and "# after\n[[models]]" in text


def test_vibe_unwire_after_the_user_edited_the_lists(home) -> None:
    """magpie vibe_test.go: stepping out takes magpie's entries out of
    [[providers]] and [[models]] even when the user added their own since."""
    from openprogram.models.gateway.agents import engine
    path = _write(home / ".vibe" / "config.toml", VIBE)
    engine.connect("vibe", {"model": "kimi/k2"})
    path.write_text(path.read_text() + '\n[[models]]\nname = "codestral"\nprovider = "mistral"\nalias = "cs"\n')
    engine.disconnect("vibe")
    doc = tomllib.loads(path.read_text())
    assert [p["name"] for p in doc["providers"]] == ["mistral"]
    assert [m["alias"] for m in doc["models"]] == ["devstral-2", "cs"]
    assert doc["active_model"] == "devstral-2"


# Crush, the rest of magpie's

def test_crush_effort_prices_and_telemetry(home) -> None:
    from openprogram.models.gateway.agents import crush, engine
    MODELS[0]["price"] = {"input": 0.6, "output": 2.5, "cache_read": 0.15, "cache_write": 0.0}
    try:
        engine.connect("crush", {"model": "kimi/k2", "effort": "high"})
    finally:
        MODELS[0].pop("price")
    large = json.loads(crush.data_path().read_text())["models"]["large"]
    assert large == {"provider": "openprogram", "model": "kimi/k2", "reasoning_effort": "high"}
    k2, gpt = json.loads(crush.config_path().read_text())["providers"]["openprogram"]["models"]
    assert (k2["cost_per_1m_in"], k2["cost_per_1m_out"], k2["cost_per_1m_in_cached"],
            k2["cost_per_1m_out_cached"]) == (0.6, 2.5, 0.0, 0.15)
    assert "cost_per_1m_in" not in gpt
    # Crush keeps API keys in its data file: one OpenProgram made is the owner's alone
    assert os.stat(crush.data_path()).st_mode & 0o777 == 0o600
    row = engine.status("crush")
    effort = [f for f in row["fields"] if f["key"] == "effort"][0]
    assert [o["value"] for o in effort["options"]] == ["low", "medium", "high"]
    assert "telemetry" not in row["values"]
    engine.set_kept("crush", {"telemetry": "off"})
    assert json.loads(crush.config_path().read_text())["options"] == {"disable_metrics": True}
    engine.disconnect("crush")
    # telemetry is about Crush's vendor: disconnecting leaves it
    assert engine.status("crush")["values"]["telemetry"] == "off"
    assert json.loads(crush.config_path().read_text())["options"] == {"disable_metrics": True}
    engine.set_kept("crush", {"telemetry": ""})
    assert json.loads(crush.config_path().read_text()) == {}


def test_crush_telemetry_kept_on_by_the_user_is_theirs(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = _write(home / ".config" / "crush" / "crush.json", '{"options": {"disable_metrics": false}}')
    engine.set_kept("crush", {"telemetry": "off"})
    assert json.loads(path.read_text()) == {"options": {"disable_metrics": False}}
    engine.set_kept("crush", {"telemetry": ""})
    assert json.loads(path.read_text()) == {"options": {"disable_metrics": False}}


# Goose, the rest of magpie's

def test_goose_effort_and_telemetry(home) -> None:
    from openprogram.models.gateway.agents import engine
    config = _write(home / ".config" / "goose" / "config.yaml", "GOOSE_PROVIDER: anthropic\n")
    engine.connect("goose", {"model": "openai/gpt-5.5", "effort": "max"})
    assert edit.load("yaml", config.read_text())["GOOSE_THINKING_EFFORT"] == "max"
    engine.set_kept("goose", {"telemetry": "off"})
    assert edit.load("yaml", config.read_text())["GOOSE_TELEMETRY_ENABLED"] is False
    engine.disconnect("goose")
    assert edit.load("yaml", config.read_text()) == {"GOOSE_PROVIDER": "anthropic", "GOOSE_TELEMETRY_ENABLED": False}
    engine.set_kept("goose", {"telemetry": ""})
    assert edit.load("yaml", config.read_text()) == {"GOOSE_PROVIDER": "anthropic"}


# jcode, the rest of magpie's

def test_jcode_profile_rewritten_as_tables_and_taken_out_whole(home) -> None:
    from openprogram.models.gateway.agents import engine
    path = _write(home / ".jcode" / "config.toml", JCODE)
    engine.connect("jcode", {"model": "kimi/k2"})
    # jcode wrote the profile back with its models as tables, and the user added a header
    doc = tomllib.loads(path.read_text())
    profile = doc["providers"]["openprogram"]
    lines = [line for line in path.read_text().splitlines(keepends=True)]
    start = next(i for i, line in enumerate(lines) if line.startswith("[providers.openprogram]"))
    tables = "".join(f'\n[[providers.openprogram.models]]\nid = "{m["id"]}"\n' for m in profile["models"])
    head = "".join(lines[:start])
    path.write_text(head + '[providers.openprogram]\ntype = "openai-compatible"\n'
                    f'base_url = "{profile["base_url"]}"\napi_key = "{profile["api_key"]}"\n'
                    'requires_api_key = true\nextra_body = { x = 1 }\n' + tables)
    engine.connect("jcode", {"model": "openai/gpt-5.5"})
    profile = tomllib.loads(path.read_text())["providers"]["openprogram"]
    assert profile["extra_body"] == {"x": 1} and profile["default_model"] == "openai/gpt-5.5"
    assert [m["id"] for m in profile["models"]] == ["kimi/k2", "openai/gpt-5.5"]
    engine.disconnect("jcode")
    doc = tomllib.loads(path.read_text())
    assert "openprogram" not in doc["providers"]
    assert doc["provider"] == {"default_provider": "deepseek", "default_model": "deepseek-chat"}


# Muse Code

def test_muse_code(home) -> None:
    from openprogram.models.gateway.agents import engine, muse
    from openprogram.models.gateway.keys import path_key, token_for
    path = _write(home / ".config" / "muse" / "settings.json",
                  '{"schema_version": 1, "model": "muse-spark", "theme": "dark",'
                  ' "endpoint_transport": {"base_url": "https://meta.example/v1", "auth": "bearer"}}')
    before = json.loads(path.read_text())
    engine.connect("muse", {"model": "kimi/k2"})
    doc = json.loads(path.read_text())
    # no Authorization at all: the Meta sign-in is never sent; its key is in its address
    assert doc["endpoint_transport"] == {"base_url": f"{GATEWAY}/agent/{path_key(token_for('muse'))}/openai/v1",
                                         "auth": "none"}
    assert doc["model"] == "kimi/k2" and doc["theme"] == "dark"
    engine.disconnect("muse")
    assert json.loads(path.read_text()) == before


def test_muse_code_new_file_goes_again(home) -> None:
    from openprogram.models.gateway.agents import engine, muse
    engine.connect("muse", {"model": "kimi/k2"})
    assert json.loads(muse.config_path().read_text())["schema_version"] == 1
    engine.disconnect("muse")
    assert not muse.config_path().exists()


def test_muse_models_list() -> None:
    from openprogram.models.gateway import conversion, models_info
    original = conversion.enabled_model_refs
    conversion.enabled_model_refs = lambda: [dict(m) for m in MODELS]
    try:
        k2, gpt = models_info.muse_models()["data"]
    finally:
        conversion.enabled_model_refs = original
    meta = k2["metadata"]["muse-code"]
    assert k2["id"] == "kimi/k2" and meta["family"] == "kimi" and meta["reasoning"] is True
    assert meta["limit"] == {"context": 262144, "output": 262144} and meta["description"] == "K2 via OpenProgram"
    gmeta = gpt["metadata"]["muse-code"]
    assert gmeta["limit"] == {"context": 128000, "output": 32000} and gmeta["attachment"] is True
    assert gmeta["modalities"]["input"] == ["text", "image"]


def test_muse_model_list_is_let_in_at_the_root(home, monkeypatch) -> None:
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    from openprogram.models.gateway import agents, attribution, models_info
    from openprogram.models.gateway.agents import engine, muse

    monkeypatch.setattr(agents, "all_agents", lambda: [muse.agent()])
    attribution.root_paths.cache_clear()
    app = FastAPI()

    @app.get("/gateway/muse-code/models")
    def listed():
        return models_info.muse_models()

    owner = OwnerAuthState.from_raw_token(bytes(range(32)), owner_principal_id="owner/install/0123456789abcdef",
                                          bind_host="127.0.0.1", port=18100, allowed_origins=())
    try:
        with TestClient(OwnerAuthMiddleware(app, auth_state=owner), base_url="http://127.0.0.1:18100",
                        client=("127.0.0.1", 50000)) as client:
            assert client.get("/muse-code/models").status_code == 401  # not connected
            engine.connect("muse", {"model": "kimi/k2"})
            answered = client.get("/muse-code/models")
            assert answered.status_code == 200 and answered.json()["data"][0]["id"] == "kimi/k2"
            # a web page reaches loopback too, and sends an Origin
            assert client.get("/muse-code/models", headers={"Origin": "https://evil.example"}).status_code != 200
            # a proxy on this computer passing a request on: any forwarded header keeps it out
            for name, value in (("Forwarded", "for=203.0.113.9"), ("X-Forwarded-For", "203.0.113.9"),
                                ("X-Forwarded-Host", "muse.example"), ("X-Forwarded-Proto", "https"),
                                ("X-Real-IP", "203.0.113.9")):
                assert client.get("/muse-code/models", headers={name: value}).status_code != 200, name
        # a Host that isn't loopback (DNS rebinding, a tunnel) stays out
        with TestClient(OwnerAuthMiddleware(app, auth_state=owner), base_url="http://muse.example:18100",
                        client=("127.0.0.1", 50000)) as client:
            assert client.get("/muse-code/models").status_code != 200
        with TestClient(OwnerAuthMiddleware(app, auth_state=owner), base_url="http://localhost:18100",
                        client=("127.0.0.1", 50000)) as client:
            assert client.get("/muse-code/models").status_code == 200
        with TestClient(OwnerAuthMiddleware(app, auth_state=owner), base_url="http://127.0.0.1:18100",
                        client=("127.0.0.1", 50000)) as client:
            # Muse's other calls are keyed: no key, no answer
            assert client.get("/gateway/agent/muse/openai/v1/models").status_code == 401
        with TestClient(OwnerAuthMiddleware(app, auth_state=owner), base_url="http://127.0.0.1:18100",
                        client=("192.168.1.9", 50000)) as client:
            assert client.get("/muse-code/models").status_code != 200
    finally:
        attribution.root_paths.cache_clear()
