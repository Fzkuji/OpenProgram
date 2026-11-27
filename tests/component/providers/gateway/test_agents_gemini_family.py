"""Gemini CLI, Antigravity CLI (agy), Cursor CLI and Devin in the agent
registry, after yetone/magpie's tests of the same agents
(``internal/agent/gemini_detect_test.go``, ``agy_test.go``,
``agy_effort_test.go``, ``devin_test.go``,
``internal/provider/devin_models_test.go``)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openprogram.models.gateway import edit


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway.agents import base
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(base, "_tool_dirs", lambda: [])
    from openprogram.models.gateway.agents import shellfn
    monkeypatch.setattr(shellfn, "shell_profiles", lambda: [shellfn.Profile("posix", tmp_path / ".zshrc", True)])
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [
        {"id": "deepseek/deepseek-chat", "name": "DS", "provider": "deepseek", "thinking_levels": []},
        {"id": "openai/gpt-5", "name": "GPT-5", "provider": "openai", "thinking_levels": ["minimal", "low", "medium", "high"]},
    ])
    return tmp_path


def _command(home: Path, name: str) -> None:
    folder = home / "bin"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)


# Gemini CLI

def test_gemini_is_found_by_its_command_not_its_folder(home) -> None:
    """Antigravity keeps its folders in ~/.gemini too (magpie gemini_detect_test.go)."""
    from openprogram.models.gateway.agents import find
    (home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (home / ".gemini" / "settings.json").write_text("{}")
    assert not find("gemini").installed()
    _command(home, "gemini")
    assert find("gemini").installed()
    assert find("gemini-cli").id == "gemini"


def test_gemini_on_the_gateway_and_back(home) -> None:
    from openprogram.models.gateway.agents import agent_of_ua, engine
    folder = home / ".gemini"
    folder.mkdir()
    settings = folder / "settings.json"
    settings.write_text('{\n  // mine\n  "security": {"auth": {"selectedType": "oauth-personal"}},\n  "ui": {"theme": "x"}\n}\n')
    env = folder / ".env"
    env.write_text("# mine\nGEMINI_API_KEY=own-key\n")
    engine.connect("gemini", {"model": "deepseek/deepseek-chat"})
    loaded = edit.load("jsonc", settings.read_text())
    assert loaded["security"]["auth"]["selectedType"] == "gemini-api-key"
    assert loaded["model"] == {"name": "deepseek/deepseek-chat"} and "// mine" in settings.read_text()
    values = edit.load("dotenv", env.read_text())
    from openprogram.models.gateway.keys import gateway_key, token_for
    # a key of its own, never the gateway's
    assert token_for("gemini") != gateway_key()
    assert values == {"GEMINI_API_KEY": token_for("gemini"),
                      "GOOGLE_GEMINI_BASE_URL": "http://127.0.0.1:18100/gateway/gemini"}
    assert engine.status("gemini")["config"]["drift"] == []
    assert agent_of_ua("GeminiCLI/0.12.0 (darwin; arm64)") == "gemini"

    engine.disconnect("gemini")
    assert edit.load("jsonc", settings.read_text()) == {"security": {"auth": {"selectedType": "oauth-personal"}},
                                                         "ui": {"theme": "x"}}
    assert edit.load("dotenv", env.read_text()) == {"GEMINI_API_KEY": "own-key"}


def test_gemini_picks_it_rewrites_are_loose_and_go_back_as_a_pair(home, monkeypatch) -> None:
    """magpie gemini.go unroute: while .env names the gateway, the user's own
    auth and model come back whatever Gemini CLI's /model and /auth wrote."""
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import engine, lifecycle
    folder = home / ".gemini"
    folder.mkdir()
    settings = folder / "settings.json"
    settings.write_text('{"security": {"auth": {"selectedType": "oauth-personal"}}, "model": {"name": "gemini-2.5-pro"}}')
    engine.connect("gemini", {"model": "deepseek/deepseek-chat"})
    loaded = edit.load("jsonc", settings.read_text())
    loaded["model"]["name"] = "openai/gpt-5"  # Gemini CLI's /model
    settings.write_text(__import__("json").dumps(loaded))
    assert engine.status("gemini")["config"]["drift"] == []
    # a new port reconnects on the same model: the pick made in Gemini CLI stays
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == ["Gemini CLI"]
    assert edit.load("jsonc", settings.read_text())["model"]["name"] == "openai/gpt-5"
    engine.disconnect("gemini")
    assert edit.load("jsonc", settings.read_text()) == {"security": {"auth": {"selectedType": "oauth-personal"}},
                                                         "model": {"name": "gemini-2.5-pro"}}


def test_a_gemini_chat_keeps_its_own_sign_in_while_the_terminal_is_on_the_gateway(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.runtime.harnesses.gemini_cli import client
    system = home / "system"
    system.mkdir()
    (system / "settings.json").write_text('{"general": {"x": 1}}')
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", str(system / "settings.json"))
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_DEFAULTS_PATH", str(system / "defaults.json"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GEMINI_BASE_URL", raising=False)
    folder = home / ".gemini"
    folder.mkdir()
    (folder / "settings.json").write_text('{"security": {"auth": {"selectedType": "oauth-personal"}}, "model": {"name": "gemini-2.5-pro"}}')
    assert client.gateway_own() is None and client.launch_env(None) == {}

    engine.connect("gemini", {"model": "deepseek/deepseek-chat"})
    assert client.gateway_own() == {"auth": "oauth-personal", "model": "gemini-2.5-pro", "key": None, "base": None}
    assert client.selected_auth() == "oauth-personal"
    env = client.launch_env(None)
    assert env["GOOGLE_GEMINI_BASE_URL"] == "" and env["GEMINI_API_KEY"] == ""
    own = json.loads(Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]).read_text())
    assert own == {"general": {"x": 1}, "security": {"auth": {"selectedType": "oauth-personal"}},
                   "model": {"name": "gemini-2.5-pro"}}


def test_a_gemini_chat_without_a_sign_in_of_its_own_gets_the_model_services_key(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import engine
    from openprogram.runtime.harnesses.gemini_cli import client
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", str(home / "none" / "settings.json"))
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_DEFAULTS_PATH", str(home / "none" / "defaults.json"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    engine.connect("gemini", {"model": "deepseek/deepseek-chat"})
    assert not client.own_sign_in()
    env = client.launch_env("services-key")
    assert env["GEMINI_API_KEY"] == "services-key"
    own = json.loads(Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]).read_text())
    assert own == {"security": {"auth": {"selectedType": "gemini-api-key"}}, "model": {"name": "auto"}}


# Antigravity CLI

def test_agy_runs_from_a_settings_file_of_its_own(home) -> None:
    """magpie agy_test.go: settings.json stays the user's; the launch command carries the gateway."""
    from openprogram.models.gateway.agents import agy, engine
    from openprogram.models.gateway.keys import token_for
    folder = home / ".gemini" / "antigravity-cli"
    folder.mkdir(parents=True)
    path = folder / "settings.json"
    mine = {"customModelsConfig": {"customModels": {"mine": {"apiProvider": "API_PROVIDER_GOOGLE_GEMINI",
                                                             "modelName": "gemini-3-pro"}}},
            "model": "mine", "modelProvider": "vertex", "trustedWorkspaces": ["/w"]}
    path.write_text(json.dumps(mine, indent=2))
    assert engine.status("agy")["launch"] == ""

    engine.connect("agy", {"model": "openai/gpt-5"})
    assert json.loads(path.read_text()) == mine
    over = folder / "openprogram-settings.json"
    written = json.loads(over.read_text())
    assert written["model"] == "openprogram/openai/gpt-5" and written["modelProvider"] == "gemini"
    models = written["customModelsConfig"]["customModels"]
    assert models["openprogram/deepseek/deepseek-chat"] == {"apiProvider": "API_PROVIDER_GOOGLE_GEMINI",
                                                            "modelName": "deepseek/deepseek-chat"}
    assert models["mine"] == mine["customModelsConfig"]["customModels"]["mine"]
    # a model that takes low, medium and high is listed once more per level (agy_effort_test.go)
    assert models["openprogram/openai/gpt-5:high"]["thinkingLevel"] == "high"
    assert "openprogram/openai/gpt-5:minimal" not in models and "openprogram/deepseek/deepseek-chat:low" not in models

    status = engine.status("agy")
    want = (f"GEMINI_API_KEY={token_for('agy')} GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:18100/gateway/gemini "
            f"agy --settings '{over}' --model 'openprogram/openai/gpt-5'")
    if os.name != "nt":
        assert status["launch"] == want and want in status["notice"]

    engine.disconnect("agy")
    assert not over.exists() and json.loads(path.read_text()) == mine


def test_agy_launch_quotes_for_each_shell() -> None:
    from openprogram.models.gateway.agents.agy import launch_command, levels
    assert launch_command("http://g", "k", "/a b/it's.json", "m", False) == (
        "GEMINI_API_KEY=k GOOGLE_GEMINI_BASE_URL=http://g agy --settings '/a b/it'\\''s.json' --model 'm'")
    assert launch_command("http://g", "k", "C:\\it's.json", "m", True) == (
        '$env:GEMINI_API_KEY="k"; $env:GOOGLE_GEMINI_BASE_URL="http://g"; '
        "agy --settings 'C:\\it''s.json' --model 'm'")
    assert levels(["high"]) == [] and levels(["HIGH", "low", "xhigh"]) == ["low", "high"]


# Cursor CLI

def test_cursor_sets_its_own_model_only(home) -> None:
    from openprogram.models.gateway.agents import engine, find
    cursor = find("cursor-agent")
    assert cursor.id == "cursor" and not cursor.gateway
    # the Cursor editor's ~/.cursor says nothing of the CLI
    (home / ".cursor" / "extensions").mkdir(parents=True)
    assert not cursor.installed()
    path = home / ".cursor" / "cli-config.json"
    path.write_text('{"permissions": {"allow": []}, "model": {"modelId": "gpt-5"}}')
    assert cursor.installed()
    status = engine.status("cursor")
    model = next(f for f in status["fields"] if f["key"] == "model")
    assert status["gateway"] is False and [o["value"] for o in model["options"]] == ["auto"]

    engine.connect("cursor", {"model": "auto"})
    loaded = json.loads(path.read_text())
    assert loaded["model"] == {"modelId": "auto", "displayModelId": "auto", "displayName": "auto"}
    assert loaded["hasChangedDefaultModel"] is True
    engine.disconnect("cursor")
    assert json.loads(path.read_text()) == {"permissions": {"allow": []}, "model": {"modelId": "gpt-5"}}


def test_own_model_agents_are_left_out_of_reseat_and_rewire(home, monkeypatch) -> None:
    from openprogram.models.gateway import address
    from openprogram.models.gateway.agents import engine, lifecycle
    (home / ".cursor").mkdir()
    engine.connect("cursor", {"model": "auto"})
    assert lifecycle.reseat(["deepseek/deepseek-chat"]) == []
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18200")
    assert lifecycle.rewire() == []


def test_own_model_route_takes_an_own_id(home, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.settings import harness_models
    app = FastAPI()
    harness_models.register(app)
    (home / ".cursor").mkdir()
    (home / ".cursor" / "cli-config.json").write_text("{}")
    answer = TestClient(app).put("/api/harnesses/models", json={"harness": "cursor", "model": "auto"})
    assert answer.status_code == 200 and answer.json()["model"] == "auto"
    assert TestClient(app).put("/api/harnesses/models", json={"harness": "gemini", "model": "auto"}).status_code == 400


# Devin

DEVIN_LIST = {"families": [
    {"family_uid": "Adaptive", "family_label": "Adaptive", "variants": [{"model_uid": "adaptive", "label": "Adaptive"}]},
    {"family_uid": "swe-2", "family_label": "SWE-2", "aliases": ["swe"], "variants": [
        {"model_uid": "swe-2-high", "label": "SWE-2 High"}, {"model_uid": "swe-2-medium", "label": "SWE-2 Medium"},
        {"model_uid": "swe-2-max", "label": "SWE-2 Max"},
        {"model_uid": "swe-2-low-fast", "label": "SWE-2 Low Fast"},
        {"model_uid": "swe-2-high-fast", "label": "SWE-2 High Fast"}]},
    {"family_uid": "glm-5.2", "family_label": "GLM-5.2", "variants": [
        {"model_uid": "glm-5-2", "label": "GLM-5.2"}, {"model_uid": "glm-5-2-max", "label": "GLM-5.2 Max"},
        {"model_uid": "glm-5-2-1m", "label": "GLM-5.2 1M"}]},
]}


@pytest.fixture
def devin_list(monkeypatch):
    from openprogram.models.gateway.agents import devin
    families = devin.parse_models(json.dumps(DEVIN_LIST))
    monkeypatch.setattr(devin, "families", lambda wait=False: families)
    return families


def test_devin_offers_families(devin_list, monkeypatch) -> None:
    """magpie TestDevinOptionsOfferFamilies."""
    from openprogram.models.gateway.agents import devin
    assert [o["value"] for o in devin.offered(devin_list)] == [
        "Adaptive", "swe-2", "swe-2-fast", "glm-5.2", "glm-5-2", "glm-5-2-1m"]
    assert devin_list[1].efforts() == ["medium", "high", "max"] and devin_list[1].efforts("fast") == ["low", "high"]
    monkeypatch.setattr(devin, "picked", lambda agent_id: {"model": "gone-model"})
    assert devin._options()[0] == ("gone-model", "gone-model")


def test_devin_split_and_pick(devin_list) -> None:
    """magpie TestDevinModelAndEffort, for the model field."""
    from openprogram.models.gateway.agents.devin import pick, split, tier_of
    assert split(devin_list, "swe-2-max") == ("swe-2", "max")
    assert split(devin_list, "swe-2-high-fast") == ("swe-2-fast", "high")
    assert split(devin_list, "glm-5-2-1m") == ("glm-5-2-1m", "")
    assert pick(devin_list, "swe-2-fast", "high") == "swe-2-high-fast"
    assert pick(devin_list, "swe-2-fast", "medium") == "swe-2-high-fast"  # a tie goes up
    assert pick(devin_list, "swe-2-fast", "") == "swe-2-high-fast"       # the family's default effort
    assert pick(devin_list, "glm-5.2", "max") == "glm-5-2-max"
    assert pick(devin_list, "glm-5.2", "") == "glm-5.2"
    assert pick(devin_list, "swe-2-medium", "") == "swe-2-medium"
    assert tier_of("swe-1-6-fast") == ("", "")


def test_devin_keeps_the_effort_and_puts_its_own_back(home, devin_list) -> None:
    from openprogram.models.gateway.agents import engine
    path = home / ".config" / "devin" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text('{\n  // mine\n  "theme_mode": "dark",\n  "agent": {"model": "swe-2-max"}\n}\n')
    assert engine.status("devin")["installed"]
    engine.connect("devin", {"model": "swe-2-fast"})
    assert edit.load("jsonc", path.read_text())["agent"]["model"] == "swe-2-high-fast"
    assert "// mine" in path.read_text()
    engine.disconnect("devin")
    assert edit.load("jsonc", path.read_text()) == {"theme_mode": "dark", "agent": {"model": "swe-2-max"}}


def test_registry_lists_the_new_agents() -> None:
    from openprogram.models.gateway.agents import all_agents
    ids = [a.id for a in all_agents()]
    assert {"gemini", "agy", "cursor", "devin"} <= set(ids)
