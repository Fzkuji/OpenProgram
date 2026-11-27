"""'Also in the terminal' edits a harness's own config surgically and puts it back."""
from __future__ import annotations

import json
import os
import tomllib

import pytest


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr("openprogram.models.gateway.address.worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    (tmp_path / "codex").mkdir()
    (tmp_path / "claude").mkdir()
    from openprogram.models.gateway import codex_app
    RESTARTS.clear()
    monkeypatch.setattr(codex_app, "base_instructions", lambda _versions: "You are Codex.")
    monkeypatch.setattr(codex_app, "restart_app", lambda home: RESTARTS.append(str(home)))
    monkeypatch.setattr("openprogram.models.gateway.conversion.enabled_model_refs", lambda: [
        {"id": "openrouter/x", "name": "X", "provider": "openrouter", "context_window": None},
        {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "context_window": 128000,
         "thinking_levels": ["low", "medium", "high"], "default_thinking_level": "high"},
    ])
    return tmp_path


#: The Codex home of each restart of the Codex app's app-server a test asked for.
RESTARTS: list[str] = []


CODEX = (
    "# my settings\r\n"
    "model = \"gpt-5.5\"  # the default\r\n"
    "approval_policy = \"on-request\"\r\n"
    "\r\n"
    "# projects I trust\r\n"
    "[projects.\"/repo\"]\r\n"
    "trust_level = \"trusted\"\r\n"
)


def test_codex_config_is_edited_in_place_and_restored(homes) -> None:
    from openprogram.models.gateway.agents import engine

    path = homes / "codex" / "config.toml"
    path.write_bytes(CODEX.encode())
    engine.connect("codex", {"model": "openprogram:deepseek/deepseek-chat"})
    text = path.read_bytes().decode()
    assert "\n" not in text.replace("\r\n", "")  # CRLF kept
    assert "# my settings" in text and "# projects I trust" in text
    assert 'model = "deepseek/deepseek-chat"  # the default' in text
    doc = tomllib.loads(text)
    assert doc["model_provider"] == "openprogram"
    assert doc["approval_policy"] == "on-request" and doc["projects"]["/repo"]["trust_level"] == "trusted"
    table = doc["model_providers"]["openprogram"]
    assert table["wire_api"] == "responses" and table["base_url"].endswith("/gateway/openai/v1")
    assert table["experimental_bearer_token"].startswith("opgw-")
    # Top-level keys stay above the first table.
    assert text.index("model_provider") < text.index("[projects")
    assert engine.status("codex")["config"]["applied"] and not engine.status("codex")["config"]["drift"]

    engine.disconnect("codex")
    restored = path.read_bytes().decode()
    doc = tomllib.loads(restored)
    assert doc["model"] == "gpt-5.5" and "model_provider" not in doc
    assert "openprogram" in doc["model_providers"]  # the table stays for threads started on it
    assert restored.startswith(CODEX.split("[projects")[0].replace('model = "gpt-5.5"', 'model = "gpt-5.5"'))
    assert not engine.status("codex")["config"]["applied"]


def test_a_value_changed_since_is_drift_and_not_restored(homes) -> None:
    from openprogram.models.gateway.agents import engine

    path = homes / "codex" / "config.toml"
    path.write_text('model = "gpt-5.5"\n', encoding="utf-8")
    engine.connect("codex", {"model": "deepseek/deepseek-chat"})
    path.write_text(path.read_text(encoding="utf-8").replace('"deepseek/deepseek-chat"', '"o3"'), encoding="utf-8")
    assert engine.status("codex")["config"]["drift"] == ["model"]
    engine.disconnect("codex")
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    assert doc["model"] == "o3" and "model_provider" not in doc


def test_unparseable_config_is_refused(homes) -> None:
    from openprogram.models.gateway.agents import engine

    path = homes / "codex" / "config.toml"
    path.write_text("model = \n[broken", encoding="utf-8")
    with pytest.raises(engine.AgentConfigError):
        engine.connect("codex", {"model": "deepseek/deepseek-chat"})
    assert path.read_text(encoding="utf-8") == "model = \n[broken"
    assert not engine.status("codex")["config"]["applied"]

    settings = homes / "claude" / "settings.json"
    settings.write_text("{not json", encoding="utf-8")
    with pytest.raises(engine.AgentConfigError):
        engine.connect("claude_code", {"model": "deepseek/deepseek-chat"})
    assert settings.read_text(encoding="utf-8") == "{not json"


def test_claude_settings_keep_order_indent_and_come_back(homes) -> None:
    from openprogram.models.gateway.agents import engine

    real = homes / "claude" / "real-settings.json"
    original = json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "env": {"CLAUDE_CODE_USE_BEDROCK": "1", "FOO": "bar"},
                           "theme": "dark"}, indent=4) + "\n"
    real.write_text(original, encoding="utf-8")
    os.symlink(real, homes / "claude" / "settings.json")

    engine.connect("claude_code", {"model": "zai/glm-5", "haiku": "deepseek/deepseek-chat"})
    assert (homes / "claude" / "settings.json").is_symlink()
    text = real.read_text(encoding="utf-8")
    data = json.loads(text)
    # a new key goes in as its object's first member, at the file's indent (magpie insertKey)
    assert list(data) == ["model", "permissions", "env", "theme"]
    assert text.startswith('{\n    "model"')
    assert data["model"] == "zai/glm-5"
    env = data["env"]
    assert "CLAUDE_CODE_USE_BEDROCK" not in env and env["FOO"] == "bar"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:18100/gateway/anthropic"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek/deepseek-chat"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "zai/glm-5" and "ANTHROPIC_MODEL" not in env

    engine.disconnect("claude_code")
    assert json.loads(real.read_text(encoding="utf-8")) == json.loads(original)
    assert real.read_text(encoding="utf-8").startswith('{\n    "permissions"')


def test_claude_settings_created_from_nothing_go_back_to_nothing(homes) -> None:
    from openprogram.models.gateway.agents import engine

    settings = homes / "claude" / "settings.json"
    settings.write_text('{"theme": "light"}\n', encoding="utf-8")
    engine.connect("claude_code", {"model": "zai/glm-5"})
    engine.disconnect("claude_code")
    assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "light"}


def test_the_codex_app_gets_the_models_and_restarts_when_the_route_changes(homes) -> None:
    from openprogram.models.gateway.agents import engine

    path = homes / "codex" / "config.toml"
    path.write_text('model = "gpt-5.5"\n')
    engine.connect("codex", {"model": "openprogram:deepseek/deepseek-chat"})
    doc = tomllib.loads(path.read_text())
    catalog = json.loads((homes / "codex" / "openprogram-models.json").read_text())
    assert doc["model_catalog_json"] == str(homes / "codex" / "openprogram-models.json")
    assert [m["slug"] for m in catalog["models"]] == ["deepseek/deepseek-chat", "openrouter/x"]
    first = catalog["models"][0]
    assert first["context_window"] == 128000 and first["visibility"] == "list"
    assert first["model_messages"]["instructions_template"] == "You are Codex."
    assert [p["effort"] for p in first["supported_reasoning_levels"]] == ["low", "medium", "high"]
    assert first["default_reasoning_level"] == "high"
    assert catalog["models"][1]["supported_reasoning_levels"] == []
    assert RESTARTS == [str(homes / "codex")]

    engine.connect("codex", {"model": "openprogram:openrouter/x"})  # a new model alone restarts nothing
    assert len(RESTARTS) == 1

    engine.disconnect("codex")
    assert "model_catalog_json" not in tomllib.loads(path.read_text())
    assert not (homes / "codex" / "openprogram-models.json").exists()
    assert len(RESTARTS) == 2


def test_the_codex_apps_conversations_move_with_its_provider_and_back(homes) -> None:
    import sqlite3
    from openprogram.models.gateway.agents import engine

    (homes / "codex" / "config.toml").write_text('model = "gpt-5.5"\n')
    db = homes / "codex" / "state_5.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE threads (id TEXT, model_provider TEXT, source TEXT)")
        connection.executemany("INSERT INTO threads VALUES (?,?,?)",
                               [("app", "openai", "vscode"), ("run", "openai", "exec")])

    def providers():
        with sqlite3.connect(db) as connection:
            return dict(connection.execute("SELECT id, model_provider FROM threads").fetchall())

    engine.connect("codex", {"model": "openprogram:deepseek/deepseek-chat"})
    assert providers() == {"app": "openprogram", "run": "openai"}
    engine.disconnect("codex")
    assert providers() == {"app": "openai", "run": "openai"}
