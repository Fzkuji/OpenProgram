"""A Gemini CLI turn against a stand-in ``gemini --acp`` that signs in and replays as the real one:
sign-in, frames, cards, approvals, Stop, resume, and a listing that starts no process."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

FAKE = Path(__file__).with_name("fake_gemini_acp.py")


@pytest.fixture
def gemini(tmp_path, monkeypatch):
    from openprogram.runtime.harnesses.gemini_cli import client
    from openprogram.runtime.harnesses import models
    from openprogram.runtime.harnesses.codex.client import ProcessPool
    import openprogram.runtime.context.session_db as session_db
    import openprogram.state.store.session.session_store as session_store_module
    from openprogram.state.store.session.session_store import SessionStore

    binary = tmp_path / "gemini"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    binary.chmod(0o755)
    log = tmp_path / "gemini.log"
    scenario = tmp_path / "scenario.json"
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    system = tmp_path / "system"
    system.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("GEMINI_CLI_BINARY", str(binary))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(home))
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", str(system / "settings.json"))
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_DEFAULTS_PATH", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("FAKE_GEMINI_LOG", str(log))
    monkeypatch.setenv("FAKE_GEMINI_SCENARIO", str(scenario))
    monkeypatch.setattr(client, "settings_key", lambda: None)
    monkeypatch.setattr(client, "_state_dir", lambda: state)
    monkeypatch.setattr(models, "_seen_options", {})
    pool = ProcessPool(starter=client.start_acp_server, idle_seconds=0)
    monkeypatch.setattr(client, "_pool", pool)
    sessions = SessionStore(tmp_path / "sessions")
    sessions.create_session("s1", "main")
    monkeypatch.setattr(session_db, "default_db", lambda: sessions)
    monkeypatch.setattr(session_store_module.shared, "_default_store", sessions)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_settings.workdir.runtime_location_for",
        lambda *_a, **_k: {"workdir": str(workdir)},
    )

    def lines(direction=None, method=None):
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        return [row for row in rows if (direction is None or row["dir"] == direction)
                and (method is None or row.get("method") == method)]

    def sign_in(kind="oauth-personal"):
        (home / ".gemini" / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": kind}}}))

    harness = SimpleNamespace(log=lines, sessions=sessions, pool=pool, workdir=workdir, home=home,
                              sign_in=sign_in, client=client, state=state, system=system)
    harness.script = lambda prompts: scenario.write_text(json.dumps(prompts))
    try:
        yield harness
    finally:
        pool.close_all()


def _request(text="hello", *, msg="m1", mode="bypass", model="gemini-cli-harness/gemini-2.5-pro"):
    return SimpleNamespace(
        session_id="s1", user_msg_id=msg, user_text=text, model_override=model,
        permission_mode=mode, thinking_effort="high", attachments=None, branch_id=None,
        agent_id="main", source="web",
    )


def _run(req, *, history=None, cancel=None, context=None):
    from openprogram.runtime.harnesses.gemini_cli.engine import GeminiCliEngine

    frames: list[dict] = []
    blocks: list[dict] = []
    result = GeminiCliEngine().run_turn(
        req=req, history=history or [], on_event=frames.append, cancel_event=cancel or threading.Event(),
        assistant_msg_id=f"{req.user_msg_id}_reply", ordered_blocks_out=blocks, execution_context=context,
    )
    return result, frames, blocks


def _events(frames):
    return [f["data"]["event"] for f in frames if f["data"].get("type") == "stream_event"]


def _chunk(kind, text):
    return {"update": {"sessionUpdate": kind, "content": {"type": "text", "text": text}}}


CALL = {"toolCallId": "c1", "title": "ls -la", "kind": "execute", "status": "pending",
        "locations": [{"path": "/work"}], "content": []}
DONE = {"update": {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
                   "content": [{"type": "content", "content": {"type": "text", "text": "a.py"}}]}}
STREAM = [
    _chunk("agent_thought_chunk", "**Listing**\nuse ls"),
    {"update": {"sessionUpdate": "tool_call", **{**CALL, "status": "in_progress"}}},
    DONE,
    {"update": {"sessionUpdate": "usage_update", "used": 1540, "size": 1048576}},
    _chunk("agent_message_chunk", "There is a.py."),
]
HELLO = [{"id": "m1", "role": "user", "content": "hello"},
         {"id": "m1_reply", "role": "assistant", "content": "one"}]


def _records(gemini):
    return (gemini.sessions.get_session("s1").get("harness_threads") or {}).get("gemini-cli-harness") or {}


def test_a_turn_streams_thoughts_cards_context_and_usage(gemini):
    gemini.sign_in()
    gemini.script([STREAM])
    (text, usage, calls), frames, blocks = _run(_request())

    assert text == "There is a.py."
    events = _events(frames)
    assert any(e["type"] == "thinking" and "use ls" in e["text"] for e in events)
    [use] = [e for e in events if e["type"] == "tool_use"]
    assert (use["tool"], json.loads(use["input"])) == ("ls -la", {"paths": ["/work"]})
    [result] = [e for e in events if e["type"] == "tool_result"]
    assert (result["result"], result["outcome"]) == ("a.py", "completed")
    assert [b["type"] for b in blocks] == ["thinking", "tool", "text"]
    assert calls[0]["tool"] == "ls -la"
    assert (usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"]) == (1500, 40, 1000)
    assert usage["context_tokens"] == 1540

    # Started in ACP mode with the person's own sign-in, nothing added to it.
    [start] = gemini.log("in", "initialize")
    assert start["argv"] == ["--acp"] and start["key"] is None and start["defaults"] is None
    [chosen] = gemini.log("in", "session/set_model")
    assert chosen["params"]["modelId"] == "gemini-2.5-pro"
    [record] = _records(gemini).values()
    assert record["seen"] == "m1_reply" and record["cwd"] == str(gemini.workdir)
    assert not gemini.pool._entries  # the process ends with its turn
    assert json.loads((gemini.state / "models.json").read_text())  # its models are kept


def test_the_next_turn_loads_the_session_and_its_replay_is_not_the_reply(gemini):
    gemini.sign_in()
    gemini.script([[_chunk("agent_message_chunk", "one")], [_chunk("agent_message_chunk", "two")]])
    _run(_request(model="gemini-cli-harness/default"))
    # The stand-in replays the session behind its answer to session/load, as older releases do.
    (text, _, calls), frames, _ = _run(_request("again", msg="m2", model="gemini-cli-harness/default"), history=HELLO)
    assert text == "two" and not calls
    assert "old" not in json.dumps(_events(frames))
    [new] = gemini.log("in", "session/new")
    [load] = gemini.log("in", "session/load")
    prompts = gemini.log("in", "session/prompt")
    assert load["params"]["sessionId"] == prompts[0]["params"]["sessionId"] == prompts[1]["params"]["sessionId"]
    assert load["pid"] != new["pid"]
    assert prompts[1]["params"]["prompt"][0]["text"] == "again"  # nothing it missed
    assert not gemini.log("in", "authenticate")


def test_a_session_that_cannot_load_starts_another_with_a_handoff(gemini):
    from openprogram.runtime.harnesses import handoff
    gemini.sign_in()
    handoff.write_record(gemini.sessions, "s1", "gemini-cli-harness", "main",
                         {"thread_id": "gone", "seen": "n1", "shared": True, "at": 1})
    gemini.script([[_chunk("agent_message_chunk", "hi")]])
    _run(_request("next"), history=[{"id": "n0", "role": "user", "content": "what is 2+2"},
                                    {"id": "n1", "role": "assistant", "content": "4"}])
    assert gemini.log("in", "session/new")
    [prompt] = gemini.log("in", "session/prompt")
    assert "what is 2+2" in prompt["params"]["prompt"][0]["text"]


def test_without_a_sign_in_the_turn_fails_as_a_sign_in(gemini):
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
    with pytest.raises(LLMError) as raised:
        _run(_request())
    assert str(raised.value).startswith("Gemini CLI: Gemini API key is missing")
    assert raised.value.reason == ErrorReason.AUTHENTICATION


def test_the_model_services_key_signs_in_new_and_loaded_sessions_without_touching_its_settings(gemini, monkeypatch):
    monkeypatch.setattr(gemini.client, "settings_key", lambda: "test-key")
    gemini.script([[_chunk("agent_message_chunk", "hi")], [_chunk("agent_message_chunk", "again")]])
    _run(_request())
    (text, _, _), _, _ = _run(_request("again", msg="m2"), history=HELLO)
    assert text == "again"
    # The second turn loads the session: Gemini CLI takes the chosen gemini-api-key from
    # OpenProgram's system-defaults layer, and the key from GEMINI_API_KEY.
    assert gemini.log("in", "session/load") and len(gemini.log("in", "session/new")) == 1
    starts = gemini.log("in", "initialize")
    assert {s["key"] for s in starts} == {"test-key"}
    defaults = json.loads(Path(starts[0]["defaults"]).read_text())
    assert defaults["security"]["auth"]["selectedType"] == "gemini-api-key"
    assert not (gemini.home / ".gemini" / "settings.json").exists()
    assert not gemini.log("in", "authenticate")


def test_the_persons_own_key_in_dot_env_is_used_and_never_replaced(gemini, monkeypatch):
    (gemini.home / ".gemini" / ".env").write_text("GEMINI_API_KEY=own\n")
    monkeypatch.setattr(gemini.client, "settings_key", lambda: "test-key")
    gemini.script([[_chunk("agent_message_chunk", "hi")], [_chunk("agent_message_chunk", "again")]])
    _run(_request())
    _run(_request("again", msg="m2"), history=HELLO)
    assert gemini.log("in", "session/load")
    assert {s["key"] for s in gemini.log("in", "initialize")} == {None}


def test_the_existing_system_defaults_are_kept(gemini, monkeypatch):
    (gemini.system / "system-defaults.json").write_text(json.dumps({"ui": {"theme": "Dracula"}}))
    env = gemini.client.launch_env("test-key")
    merged = json.loads(Path(env["GEMINI_CLI_SYSTEM_DEFAULTS_PATH"]).read_text())
    assert merged == {"ui": {"theme": "Dracula"}, "security": {"auth": {"selectedType": "gemini-api-key"}}}


def test_an_auth_type_the_person_chose_is_their_own_sign_in(gemini, monkeypatch):
    # A key Gemini CLI saved itself (its /auth dialog keeps it in the keychain) is theirs too.
    (gemini.home / ".gemini" / "settings.json").write_text(
        '// comment\n{"security": {"auth": {"selectedType": "gemini-api-key"}}}')
    monkeypatch.setattr(gemini.client, "settings_key", lambda: "test-key")
    assert gemini.client.selected_auth() == "gemini-api-key"
    assert gemini.client.sign_in_now() == (True, None)
    assert gemini.client.launch_env(None) == {}
    # Chosen in the system settings layer counts as well.
    (gemini.home / ".gemini" / "settings.json").unlink()
    (gemini.system / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": "vertex-ai"}}}))
    assert gemini.client.sign_in_now() == (True, None)


def test_a_failed_prompt_is_the_providers_error_and_the_session_keeps_the_message(gemini):
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
    gemini.sign_in()
    gemini.script([[{"error": "Rate limit exceeded. Try again later."}], [_chunk("agent_message_chunk", "ok")]])
    with pytest.raises(LLMError) as raised:
        _run(_request())
    assert "Gemini CLI: Rate limit exceeded" in str(raised.value)
    assert raised.value.reason == ErrorReason.RATE_LIMIT
    # The next turn does not hand over the message the session already holds.
    _run(_request("again", msg="m2"), history=[{"id": "m1", "role": "user", "content": "hello"}])
    prompts = gemini.log("in", "session/prompt")
    assert prompts[1]["params"]["prompt"][0]["text"] == "again"


@pytest.mark.parametrize("outcome,option", [("allow", "proceed_once"), ("deny", "cancel")])
def test_a_permission_request_opens_its_card_and_waits_for_the_person(gemini, monkeypatch, outcome, option):
    from openprogram.runtime.permissions.approvals import live
    asked = []

    def ask(owner, **kwargs):
        asked.append(kwargs)
        return live.LiveDecision(outcome, "once")

    monkeypatch.setattr(live, "ask_live_approval", ask)
    monkeypatch.setattr(live, "live_owner_from", lambda *_a: object())
    gemini.sign_in()
    # Asked before any tool_call of it, as older Gemini CLI releases do.
    gemini.script([[{"permission": CALL}, DONE]])
    (_, _, calls), frames, _ = _run(_request(mode="ask"))
    assert asked[0]["tool"] == "ls -la" and asked[0]["risk_level"] == "high"
    [answer] = [row for row in gemini.log("in") if "result" in row and row.get("id", 0) >= 900]
    assert answer["result"]["outcome"]["optionId"] == option
    assert [e["type"] for e in _events(frames)] == ["tool_use", "tool_result"]
    assert calls[0]["tool_call_id"] == "c1"


def test_full_access_allows_without_asking(gemini):
    gemini.sign_in()
    gemini.script([[{"update": {"sessionUpdate": "tool_call", **CALL}}, {"permission": CALL}, DONE]])
    _run(_request(mode="bypass"))
    [answer] = [row for row in gemini.log("in") if "result" in row and row.get("id", 0) >= 900]
    assert answer["result"]["outcome"]["optionId"] == "proceed_once"


def test_stop_cancels_the_prompt_and_keeps_what_came(gemini):
    from openprogram.runtime.harnesses.gemini_cli.engine import GeminiCliEngine
    gemini.sign_in()
    cancel = threading.Event()
    stopped: list[float] = []
    gemini.script([[_chunk("agent_message_chunk", "partial"), {"wait_cancel": True}]])

    def on_event(frame):
        if not cancel.is_set() and "partial" in json.dumps(frame):
            stopped.append(time.monotonic())
            cancel.set()

    req = _request()
    text, _, _ = GeminiCliEngine().run_turn(
        req=req, history=[], on_event=on_event, cancel_event=cancel,
        assistant_msg_id=f"{req.user_msg_id}_reply", ordered_blocks_out=[], execution_context=None)
    assert text == "partial" and time.monotonic() - stopped[0] < 5
    assert gemini.log("in", "session/cancel")


def test_the_listing_starts_no_process(gemini, tmp_path, monkeypatch):
    from openprogram.runtime.harnesses import models
    # An npm install: the launcher links to the package's script.
    package = tmp_path / "lib" / "node_modules" / "@google" / "gemini-cli"
    (package / "dist").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "@google/gemini-cli", "version": "0.71.1"}))
    script = package / "dist" / "index.js"
    script.write_text("")
    launcher = tmp_path / "bin" / "gemini"
    launcher.parent.mkdir()
    launcher.symlink_to(script)
    monkeypatch.setenv("GEMINI_CLI_BINARY", str(launcher))

    entry = models.read_gemini_cli()
    assert (entry["installed"], entry["version"], entry["signedIn"]) == (True, "0.71.1", False)
    assert [m["id"] for m in entry["models"]] == ["default"]  # Gemini CLI's own choice
    assert not gemini.log()

    # Once a turn has seen its models, they are listed, after a restart too.
    gemini.client.save_options(gemini.client.options_of({"models": {
        "availableModels": [{"modelId": "auto-gemini-2.5", "name": "Auto"}, {"modelId": "gemini-2.5-pro"}],
        "currentModelId": "auto-gemini-2.5"}}))
    gemini.sign_in()
    entry = models.read_gemini_cli()
    assert entry["signedIn"] is True
    assert [m["id"] for m in entry["models"]] == ["auto-gemini-2.5", "gemini-2.5-pro"]
    assert [m["id"] for m in entry["models"] if m["default"]] == ["auto-gemini-2.5"]
    assert all(m["efforts"] == [] for m in entry["models"])
    assert not gemini.log()


def test_not_installed(monkeypatch, tmp_path):
    from openprogram.runtime.harnesses.gemini_cli import client
    from openprogram.runtime.harnesses import models
    monkeypatch.setenv("GEMINI_CLI_BINARY", str(tmp_path / "missing"))
    assert client.binary_path() is None
    assert models.read_gemini_cli()["installed"] is False


def test_registered_as_a_harness():
    from openprogram.runtime.harnesses import HARNESS_IDS, engine_for, provider_of
    assert provider_of("gemini-cli") == "gemini-cli-harness"
    assert HARNESS_IDS["gemini-cli-harness"] == "gemini-cli"
    assert engine_for("gemini-cli-harness").label == "Gemini CLI"
