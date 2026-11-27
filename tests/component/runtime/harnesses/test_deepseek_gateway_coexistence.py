"""OpenProgram's own DeepSeek Harness chats beside the person's dsh put on
OpenProgram's gateway: they run on dsh's own models and the person's own
sign-in, never through the gateway, and the gateway's key in dsh's key store
is no sign-in of the person's."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

FAKE = Path(__file__).with_name("fake_dsh_acp.py")

OWN = ("- id: llm-pi-ai\n  name: \"@deepseek-ai/dsh-llm-pi-ai\"\n  config:\n    providers:\n      mine:\n"
       "        displayName: Mine\n        apiKeyEnv: MINE_API_KEY\n        api: openai-completions\n"
       "        baseURL: https://mine.example/v1\n        models:\n          - id: m1\n"
       "- id: agent-default-model\n  config:\n    provider: mine\n    model: m1\n    reasoningEffort: high\n")

MODELS = [{"id": "deepseek/pro", "name": "pro", "provider": "deepseek", "api": "openai-completions"}]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A dsh home whose acp profile has the person's own route and start,
    connected to OpenProgram's gateway."""
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway.agents import dsh, engine
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [dict(m) for m in MODELS])
    monkeypatch.setattr(dsh, "desktop_installed", lambda: False)
    monkeypatch.setattr(dsh, "dsh_version", lambda: "")
    monkeypatch.setattr(dsh, "app_version", lambda: "")
    acp = tmp_path / "dsh" / "profiles" / "acp" / "cordis.patch.yml"
    acp.parent.mkdir(parents=True)
    acp.write_text(OWN)
    engine.connect("dsh", {"model": "deepseek/pro"})
    doc = yaml.safe_load(acp.read_text())
    assert next(i for i in doc if i["id"] == "agent-default-model")["config"]["provider"] == "openprogram"
    return SimpleNamespace(home=tmp_path / "dsh", acp=acp, engine=engine)


def test_the_gateway_key_is_no_sign_in(wired) -> None:
    from openprogram.runtime.harnesses.deepseek import client
    creds = wired.home / ".credentials.yaml"
    assert set(yaml.safe_load(creds.read_text())["refs"]) == {"OPENPROGRAM_GATEWAY_KEY"}
    assert not client.signed_in()
    creds.write_text(creds.read_text() + "records:\n  DEEPSEEK_API_KEY:\n    kind: api-key\n")
    assert client.signed_in()
    creds.unlink()
    assert not client.signed_in()


def test_the_chats_get_the_persons_own_routes_and_start(wired) -> None:
    from openprogram.runtime.harnesses.deepseek import client
    overlay = client.gateway_overlay()
    pi = next(entry for entry in overlay if entry["id"] == "llm-pi-ai")
    assert set(pi["config"]["providers"]) == {"mine"}
    start = next(entry for entry in overlay if entry["id"] == "agent-default-model")
    assert start["config"] == {"provider": "mine", "model": "m1", "reasoningEffort": "high"}
    args = client.launch_args()
    assert args[0] == "--patch"
    assert yaml.safe_load(Path(args[1]).read_text()) == overlay
    assert client.own_model() == "mine:m1"
    assert client.gateway_model("openprogram:deepseek/pro") and not client.gateway_model("mine:m1")
    # a model on OpenProgram's routes is never offered to the chats
    options = [{"id": "model", "currentValue": '["mine","m1"]', "options": [
        {"value": '["openprogram","deepseek/pro"]', "name": "pro"}, {"value": '["mine","m1"]', "name": "m1"}]}]
    assert [m["id"] for m in client.read_options(options)[0]] == ["mine:m1"]

    # disconnected, dsh is the person's own again: no overlay
    wired.engine.disconnect("dsh")
    assert wired.acp.read_text() == OWN
    assert client.gateway_overlay() is None and client.launch_args() == []


def test_a_start_not_known_is_dshs_own(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh"))
    from openprogram.runtime.harnesses.deepseek import client
    home = tmp_path / "dsh"
    home.mkdir()
    # wired by another OpenProgram (no stash here), in the home patch
    (home / "cordis.patch.yml").write_text(
        "- id: agent-default-model # openprogram\n  config:\n    provider: openprogram-responses\n    model: x/y\n")
    # the start is the one the installed dsh ships, as it prints it
    binary = tmp_path / "dsh-bin"
    binary.write_text("#!/bin/sh\n[ \"$3\" = --dump-default-config ] || exit 2\n"
                      "printf -- '- id: agent-default-model\\n  config:\\n    provider: shipped\\n    model: m\\n'\n")
    binary.chmod(0o755)
    monkeypatch.setenv("DSH_BINARY", str(binary))
    assert client.gateway_overlay() == [{"id": "agent-default-model", "config": {"provider": "shipped", "model": "m"}}]
    # one dsh can't say is named by nobody: no start is made up
    binary.write_text("#!/bin/sh\nexit 1\n")
    import os
    os.utime(binary, (1, 1))
    assert client.gateway_overlay() is None and client.own_model() == ""


def test_a_turn_runs_on_the_overlay_and_never_on_a_gateway_model(wired, tmp_path, monkeypatch) -> None:
    from openprogram.runtime.harnesses.codex.client import ProcessPool
    from openprogram.runtime.harnesses.deepseek import client
    from openprogram.runtime.harnesses.deepseek.engine import DeepSeekHarnessEngine
    import openprogram.runtime.context.session_db as session_db
    import openprogram.state.store.session.session_store as session_store_module
    from openprogram.state.store.session.session_store import SessionStore

    argv = tmp_path / "argv.txt"
    log = tmp_path / "dsh.log"
    binary = tmp_path / "dsh-bin"
    binary.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{argv}"\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    binary.chmod(0o755)
    monkeypatch.setenv("DSH_BINARY", str(binary))
    monkeypatch.setenv("FAKE_DSH_LOG", str(log))
    pool = ProcessPool(starter=client.start_acp_server)
    monkeypatch.setattr(client, "_pool", pool)
    sessions = SessionStore(tmp_path / "sessions")
    sessions.create_session("s1", "main")
    monkeypatch.setattr(session_db, "default_db", lambda: sessions)
    monkeypatch.setattr(session_store_module.shared, "_default_store", sessions)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setattr("openprogram.runtime.conversation.session_settings.workdir.runtime_location_for",
                        lambda *_a, **_k: {"workdir": str(workdir)})
    req = SimpleNamespace(session_id="s1", user_msg_id="m1", user_text="hello",
                          model_override="deepseek-harness/openprogram:deepseek/pro", permission_mode="bypass",
                          thinking_effort=None, attachments=None, branch_id=None, agent_id="main", source="web")
    try:
        DeepSeekHarnessEngine().run_turn(req=req, history=[], on_event=lambda _f: None,
                                         cancel_event=threading.Event(), assistant_msg_id="m1_reply",
                                         ordered_blocks_out=[], execution_context=None)
    finally:
        pool.close_all()
    args = argv.read_text().split("\n")
    assert args[:3] == ["--profile", "acp", "--patch"]
    assert "llm-pi-ai" in Path(args[3]).read_text()
    sent = [json.loads(line) for line in log.read_text().splitlines()]
    picks = [row["params"]["value"] for row in sent
             if row.get("dir") == "in" and row.get("method") == "session/set_config_option"
             and row["params"]["configId"] == "model"]
    assert not any("openprogram" in value for value in picks)


SHIPPED = ("- id: web-search-deepseek\n  name: \"@deepseek-ai/dsh-web-search-deepseek\"\n  config:\n"
           "    apiKeyEnv: DEEPSEEK_API_KEY\n    baseURL: https://api.deepseek.com/anthropic\n"
           "- id: web\n  name: \"@deepseek-ai/dsh-web\"\n  config:\n    searchProvider: auto\n"
           "- id: agent-default-model\n  config:\n    provider: shipped\n    model: m\n")


def _stacked(*layers) -> dict:
    """Each row's config after every layer: a later entry's config replaces
    the earlier one's, as dsh applies its layers."""
    rows: dict = {}
    for layer in layers:
        for entry in (yaml.safe_load(layer) if isinstance(layer, str) else layer) or []:
            if "config" in entry:
                rows[entry["id"]] = entry["config"]
    return rows


def _urls(value) -> list:
    if isinstance(value, dict):
        return [u for k, v in value.items() for u in ([v] if k == "baseURL" else _urls(v))]
    if isinstance(value, list):
        return [u for v in value for u in _urls(v)]
    return []


def _shipping(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "dsh-shipped"
    binary.write_text("#!/bin/sh\n[ \"$3\" = --dump-default-config ] || exit 2\ncat <<'X'\n" + SHIPPED + "X\n")
    binary.chmod(0o755)
    monkeypatch.setenv("DSH_BINARY", str(binary))


def test_the_chats_search_on_dshs_own_search_not_the_gateway(wired, tmp_path, monkeypatch) -> None:
    from openprogram.runtime.harnesses.deepseek import client
    _shipping(tmp_path, monkeypatch)
    live = wired.acp.read_text()
    # connected, dsh's own search goes through OpenProgram's gateway
    assert any("/gateway/" in url for url in _urls(_stacked(SHIPPED, live)))
    rows = _stacked(SHIPPED, live, client.gateway_overlay())
    assert not any("/gateway/" in url for url in _urls(rows))
    # the person had none of their own: as dsh ships them
    assert rows["web-search-deepseek"]["baseURL"] == "https://api.deepseek.com/anthropic"
    assert rows["web"] == {"searchProvider": "auto"}


def test_the_chats_search_as_the_person_had_it(wired, tmp_path, monkeypatch) -> None:
    import os
    from openprogram.models.gateway import stash
    from openprogram.runtime.harnesses.deepseek import client
    _shipping(tmp_path, monkeypatch)
    copy = Path(stash.entry("dsh")["files"][os.path.realpath(wired.acp)]["copy"])
    copy.write_text(copy.read_text() + "- id: web\n  config:\n    searchProvider: exa\n")
    rows = _stacked(SHIPPED, wired.acp.read_text(), client.gateway_overlay())
    assert rows["web"] == {"searchProvider": "exa"}
    assert not any("/gateway/" in url for url in _urls(rows))
