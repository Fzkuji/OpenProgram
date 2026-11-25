"""A Claude Code turn against a stand-in ``claude -p``: frames, cards, one process per conversation,
settings changed in the running process, permission prompts, Stop, resume and fork."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

FAKE = Path(__file__).with_name("fake_claude_code.py")


@pytest.fixture
def claude(tmp_path, monkeypatch):
    from openprogram.runtime.harnesses.claude_code import client
    import openprogram.runtime.context.session_db as session_db
    import openprogram.state.store.session.session_store as session_store_module
    from openprogram.state.store.session.session_store import SessionStore

    binary = tmp_path / "claude"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    binary.chmod(0o755)
    log = tmp_path / "claude.log"
    scenario = tmp_path / "scenario.json"
    monkeypatch.setenv("OPENPROGRAM_CLAUDE_BINARY", str(binary))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", str(scenario))
    monkeypatch.setenv("FAKE_CLAUDE_STATE", str(tmp_path / "turns"))
    pool = client.Pool()
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

    def lines():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    harness = SimpleNamespace(log=lines, sessions=sessions, pool=pool, workdir=workdir, tmp=tmp_path)
    harness.script = lambda turns: scenario.write_text(json.dumps(turns))
    harness.inputs = lambda: [row["in"] for row in lines() if "in" in row]
    harness.launches = lambda: [row for row in lines() if "argv" in row]
    try:
        yield harness
    finally:
        pool.close_all()


def _request(text="hello", *, msg="m1", mode="bypass", effort="high", model="claude-code-harness/sonnet"):
    return SimpleNamespace(
        session_id="s1", user_msg_id=msg, user_text=text, model_override=model,
        permission_mode=mode, thinking_effort=effort, attachments=None, branch_id=None,
        agent_id="main", source="web",
    )


def _run(req, *, history=None, cancel=None, context=None):
    from openprogram.runtime.harnesses.claude_code.engine import ClaudeCodeEngine

    frames: list[dict] = []
    blocks: list[dict] = []
    result = ClaudeCodeEngine().run_turn(
        req=req, history=history or [], on_event=frames.append, cancel_event=cancel or threading.Event(),
        assistant_msg_id=f"{req.user_msg_id}_reply", ordered_blocks_out=blocks, execution_context=context,
    )
    return result, frames, blocks


def _events(frames):
    return [f["data"]["event"] for f in frames if f["data"].get("type") == "stream_event"]


def _delta(kind, text):
    key = "text" if kind == "text_delta" else "thinking"
    return {"send": {"type": "stream_event", "parent_tool_use_id": None,
                     "event": {"type": "content_block_delta", "delta": {"type": kind, key: text}}}}


def _start(message_id):
    return {"send": {"type": "stream_event", "parent_tool_use_id": None,
                     "event": {"type": "message_start", "message": {"id": message_id}}}}


def _assistant(uuid_, message_id, content, usage=None):
    body = {"id": message_id, "model": "claude-test", "content": content}
    if usage:
        body["usage"] = usage
    return {"send": {"type": "assistant", "uuid": uuid_, "parent_tool_use_id": None, "message": body}}


TURN = [
    _start("msg_1"), _delta("thinking_delta", "look first"),
    _assistant("u1", "msg_1", [{"type": "thinking", "thinking": "look first"}]),
    _assistant("u2", "msg_1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}],
               usage={"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}),
    {"send": {"type": "user", "parent_tool_use_id": None, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "a.py", "is_error": False}]}}},
    {"send": {"type": "assistant", "uuid": "sub", "parent_tool_use_id": "t9", "message": {
        "id": "msg_sub", "content": [{"type": "text", "text": "a subagent's step"}]}}},
    _start("msg_2"), _delta("text_delta", "There is "), _delta("text_delta", "a.py."),
    _assistant("u3", "msg_2", [{"type": "text", "text": "There is a.py."}]),
]


def _records(claude):
    return (claude.sessions.get_session("s1").get("harness_threads") or {}).get("claude-code-harness") or {}


def test_a_turn_streams_thinking_cards_and_text(claude):
    claude.script([TURN])
    (text, usage, calls), frames, blocks = _run(_request())

    assert text == "There is a.py."
    events = _events(frames)
    assert {"type": "thinking", "text": "look first"} in events
    [use] = [e for e in events if e["type"] == "tool_use"]
    assert (use["tool"], json.loads(use["input"])) == ("Bash", {"command": "ls"})
    [result] = [e for e in events if e["type"] == "tool_result"]
    assert result["result"] == "a.py"
    assert [b["type"] for b in blocks] == ["thinking", "tool", "text"]
    assert "a subagent's step" not in text and usage["context_tokens"] == 1010

    [launch] = claude.launches()
    argv = launch["argv"]
    assert launch["cwd"] == str(claude.workdir)
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--allow-dangerously-skip-permissions" in argv and "--session-id" in argv
    [record] = _records(claude).values()
    assert record["thread_id"] == argv[argv.index("--session-id") + 1]
    # The person's message (its uuid as sent) and the assistant records it led to.
    [sent] = [m for m in claude.inputs() if m.get("type") == "user"]
    assert record["own_turns"] == [sent["uuid"], "u1", "u2", "u3"]


def test_the_next_turn_goes_into_the_same_process_with_new_settings(claude):
    claude.script([[_assistant("a", "m", [{"type": "text", "text": "one"}])],
                   [_assistant("b", "n", [{"type": "text", "text": "two"}])]])
    _run(_request())
    history = [{"id": "m1", "role": "user", "content": "hello"},
               {"id": "m1_reply", "role": "assistant", "content": "one"}]
    (text, _, _), _, _ = _run(_request("again", msg="m2", model="claude-code-harness/opus", effort="low",
                                       mode="acceptEdits"), history=history)
    assert text == "two"
    assert len(claude.launches()) == 1
    controls = [m["request"] for m in claude.inputs() if m.get("type") == "control_request"]
    assert {"subtype": "set_model", "model": "opus"} in controls
    assert {"subtype": "apply_flag_settings", "settings": {"effortLevel": "low"}} in controls
    assert {"subtype": "set_permission_mode", "mode": "acceptEdits"} in controls
    users = [m for m in claude.inputs() if m.get("type") == "user"]
    assert users[1]["message"]["content"][0]["text"] == "again"  # nothing it missed


def test_a_synced_session_resumes_in_its_folder(claude):
    from openprogram.runtime.harnesses import handoff
    folder = claude.tmp / "repo"
    folder.mkdir()
    claude.sessions.update_session("s1", imported_from={"agent": "claude-code", "cwd": str(folder)})
    handoff.write_record(claude.sessions, "s1", "claude-code-harness", "main",
                         {"thread_id": "from-app", "seen": "n1", "shared": True, "at": 1})
    claude.script([[_assistant("x", "m", [{"type": "text", "text": "hi"}])]])
    _run(_request(), history=[{"id": "n1", "role": "assistant", "content": "earlier"}])
    [launch] = claude.launches()
    assert launch["argv"][launch["argv"].index("--resume") + 1] == "from-app" and launch["cwd"] == str(folder)


def test_a_failed_result_is_the_providers_error(claude):
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
    claude.script([[{"fail": "Claude usage limit reached"}]])
    with pytest.raises(LLMError) as raised:
        _run(_request())
    assert "usage limit" in str(raised.value) and raised.value.reason == ErrorReason.RATE_LIMIT


@pytest.mark.parametrize("outcome,behavior", [("allow", "allow"), ("deny", "deny")])
def test_a_permission_prompt_waits_for_the_person(claude, monkeypatch, outcome, behavior):
    from openprogram.runtime.permissions.approvals import live
    asked = []

    def ask(owner, **kwargs):
        asked.append(kwargs)
        return live.LiveDecision(outcome, "once")

    monkeypatch.setattr(live, "ask_live_approval", ask)
    monkeypatch.setattr(live, "live_owner_from", lambda *_a: object())
    claude.script([[{"ask": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "rm x"}}}]])
    _run(_request(mode="ask"))
    assert asked[0]["tool"] == "Bash" and asked[0]["arguments"] == {"command": "rm x"}
    [answer] = [m for m in claude.inputs() if m.get("type") == "control_response"]
    assert answer["response"]["response"]["behavior"] == behavior


def test_stop_interrupts_and_keeps_the_process(claude):
    cancel = threading.Event()
    claude.script([[_start("m"), _delta("text_delta", "partial"), {"wait_interrupt": True}]])
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    (text, _, _), _, _ = _run(_request(), cancel=cancel)
    assert text == "partial" and time.monotonic() - started < 5
    assert any(m.get("request", {}).get("subtype") == "interrupt" for m in claude.inputs())
    assert len(claude.pool._entries) == 1


def test_effort_maps_to_the_nearest_level():
    from openprogram.runtime.harnesses.claude_code.engine import claude_effort
    assert claude_effort("off") == "low" and claude_effort("xhigh") == "xhigh"
    assert claude_effort("medium", ["low", "high"]) == "high" and claude_effort("auto") is None


def test_a_transcript_turn_is_known_by_its_last_assistant_record(tmp_path):
    from openprogram.state.store.session.external_import import claude_code
    path = tmp_path / "s.jsonl"
    rows = [{"type": "user", "uuid": "q", "parentUuid": None, "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "uuid": "r1", "parentUuid": "q", "message": {"content": [{"type": "text", "text": "a"}]}},
            {"type": "assistant", "uuid": "r2", "parentUuid": "r1", "message": {"content": [{"type": "text", "text": "b"}]}}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert claude_code.parse(path).turns[0].turn_id == "r2"
