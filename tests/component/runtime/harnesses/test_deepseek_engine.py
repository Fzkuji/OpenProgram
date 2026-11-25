"""A DeepSeek Harness turn against a stand-in ``dsh --profile acp``: frames, cards, approvals, Stop, resume."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

FAKE = Path(__file__).with_name("fake_dsh_acp.py")


@pytest.fixture
def dsh(tmp_path, monkeypatch):
    from openprogram.runtime.harnesses.deepseek import client
    from openprogram.runtime.harnesses.codex.client import ProcessPool
    import openprogram.runtime.context.session_db as session_db
    import openprogram.state.store.session.session_store as session_store_module
    from openprogram.state.store.session.session_store import SessionStore

    binary = tmp_path / "dsh"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    binary.chmod(0o755)
    log = tmp_path / "dsh.log"
    scenario = tmp_path / "scenario.json"
    monkeypatch.setenv("DSH_BINARY", str(binary))
    monkeypatch.setenv("FAKE_DSH_LOG", str(log))
    monkeypatch.setenv("FAKE_DSH_SCENARIO", str(scenario))
    pool = ProcessPool(starter=client.start_acp_server)
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

    harness = SimpleNamespace(log=lines, sessions=sessions, pool=pool, workdir=workdir)
    harness.script = lambda prompts: scenario.write_text(json.dumps(prompts))
    try:
        yield harness
    finally:
        pool.close_all()


def _request(text="hello", *, msg="m1", mode="bypass", effort="high",
             model="deepseek-harness/deepseek-official:deepseek-pro"):
    return SimpleNamespace(
        session_id="s1", user_msg_id=msg, user_text=text, model_override=model,
        permission_mode=mode, thinking_effort=effort, attachments=None, branch_id=None,
        agent_id="main", source="web",
    )


def _run(req, *, history=None, cancel=None, context=None):
    from openprogram.runtime.harnesses.deepseek.engine import DeepSeekHarnessEngine

    frames: list[dict] = []
    blocks: list[dict] = []
    result = DeepSeekHarnessEngine().run_turn(
        req=req, history=history or [], on_event=frames.append, cancel_event=cancel or threading.Event(),
        assistant_msg_id=f"{req.user_msg_id}_reply", ordered_blocks_out=blocks, execution_context=context,
    )
    return result, frames, blocks


def _events(frames):
    return [f["data"]["event"] for f in frames if f["data"].get("type") == "stream_event"]


def _chunk(kind, message_id, text):
    return {"update": {"sessionUpdate": kind, "messageId": message_id, "content": {"type": "text", "text": text}}}


STREAM = [
    _chunk("agent_thought_chunk", "msg-1", "use ls"),
    {"update": {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "bash", "kind": "other",
                "status": "in_progress", "rawInput": {"command": "ls"}}},
    {"update": {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
                "content": [{"type": "content", "content": {"type": "text", "text": "a.py"}}]}},
    {"update": {"sessionUpdate": "usage_update", "used": 1200, "size": 128000}},
    _chunk("agent_message_chunk", "msg-2", "There is a.py."),
]


def _records(dsh):
    return (dsh.sessions.get_session("s1").get("harness_threads") or {}).get("deepseek-harness") or {}


def test_a_turn_streams_thoughts_cards_and_context(dsh):
    dsh.script([STREAM])
    (text, usage, calls), frames, blocks = _run(_request())

    assert text == "There is a.py."
    events = _events(frames)
    assert {"type": "thinking", "text": "use ls"} in events
    [use] = [e for e in events if e["type"] == "tool_use"]
    assert (use["tool"], json.loads(use["input"])) == ("bash", {"command": "ls"})
    [result] = [e for e in events if e["type"] == "tool_result"]
    assert (result["result"], result["outcome"]) == ("a.py", "completed")
    assert [b["type"] for b in blocks] == ["thinking", "tool", "text"]
    assert calls[0]["tool"] == "bash" and usage["context_tokens"] == 1200
    context = [f["data"] for f in frames if f["data"].get("type") == "context_usage"]
    assert context[0]["context_window"] == 128000

    # The branch's model and effort are set on the session; the session is kept.
    sets = [row["params"] for row in dsh.log("in", "session/set_config_option")]
    assert {"configId": "model", "value": '["deepseek-official","deepseek-pro"]'} in [
        {k: s[k] for k in ("configId", "value")} for s in sets]
    [record] = _records(dsh).values()
    assert record["own_turns"] == ["msg-1", "msg-2"] and record["seen"] == "m1_reply"
    assert record["cwd"] == str(dsh.workdir)


def test_the_next_turn_goes_on_in_the_same_session(dsh):
    dsh.script([[_chunk("agent_message_chunk", "a", "one")], [_chunk("agent_message_chunk", "b", "two")]])
    _run(_request())
    history = [{"id": "m1", "role": "user", "content": "hello"},
               {"id": "m1_reply", "role": "assistant", "content": "one"}]
    (text, _, _), _, _ = _run(_request("again", msg="m2"), history=history)
    assert text == "two"
    assert len(dsh.log("in", "session/new")) == 1
    prompts = [row["params"] for row in dsh.log("in", "session/prompt")]
    assert prompts[0]["sessionId"] == prompts[1]["sessionId"]
    assert prompts[1]["prompt"][0]["text"] == "again"  # nothing it missed


def test_a_synced_session_is_resumed_in_its_folder(dsh):
    from openprogram.runtime.harnesses import handoff
    dsh.sessions.update_session("s1", imported_from={"agent": "deepseek-harness", "cwd": "/work/ds"})
    handoff.write_record(dsh.sessions, "s1", "deepseek-harness", "main",
                         {"thread_id": "from-app", "seen": "n1", "shared": True, "at": 1})
    dsh.script([[_chunk("agent_message_chunk", "x", "hi")]])
    _run(_request(), history=[{"id": "n1", "role": "assistant", "content": "earlier"}])
    [resume] = dsh.log("in", "session/resume")
    assert resume["params"]["sessionId"] == "from-app" and resume["params"]["cwd"] == "/work/ds"
    assert not dsh.log("in", "session/new")


def test_a_session_that_cannot_resume_starts_another_with_a_handoff(dsh):
    from openprogram.runtime.harnesses import handoff
    handoff.write_record(dsh.sessions, "s1", "deepseek-harness", "main",
                         {"thread_id": "gone", "seen": "n1", "shared": True, "at": 1})
    dsh.script([[_chunk("agent_message_chunk", "x", "hi")]])
    _run(_request("next"), history=[{"id": "n0", "role": "user", "content": "what is 2+2"},
                                    {"id": "n1", "role": "assistant", "content": "4"}])
    assert dsh.log("in", "session/new")
    [prompt] = dsh.log("in", "session/prompt")
    assert "what is 2+2" in prompt["params"]["prompt"][0]["text"]


def test_a_failed_prompt_is_the_providers_error(dsh):
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
    dsh.script([[{"error": "Insufficient Balance"}]])
    with pytest.raises(LLMError) as raised:
        _run(_request())
    assert "Insufficient Balance" in str(raised.value)
    assert raised.value.reason == ErrorReason.RATE_LIMIT


@pytest.mark.parametrize("outcome,option", [("allow", "allow-once"), ("deny", "reject-once")])
def test_a_permission_request_waits_for_the_person(dsh, monkeypatch, outcome, option):
    from openprogram.runtime.permissions.approvals import live
    asked = []

    def ask(owner, **kwargs):
        asked.append(kwargs)
        return live.LiveDecision(outcome, "once")

    monkeypatch.setattr(live, "ask_live_approval", ask)
    monkeypatch.setattr(live, "live_owner_from", lambda *_a: object())
    dsh.script([[STREAM[1], {"permission": "c1"}, STREAM[2]]])
    _run(_request(mode="ask"))
    assert asked[0]["tool"] == "bash" and asked[0]["arguments"] == {"command": "ls"}
    [answer] = [row for row in dsh.log("in") if "result" in row and row.get("id", 0) >= 900]
    assert answer["result"]["outcome"]["optionId"] == option


def test_full_access_allows_without_asking(dsh):
    dsh.script([[STREAM[1], {"permission": "c1"}, STREAM[2]]])
    _run(_request(mode="bypass"))
    [answer] = [row for row in dsh.log("in") if "result" in row and row.get("id", 0) >= 900]
    assert answer["result"]["outcome"]["optionId"] == "allow-once"


def test_stop_cancels_the_prompt_and_keeps_what_came(dsh):
    from openprogram.runtime.harnesses.deepseek.engine import DeepSeekHarnessEngine
    cancel = threading.Event()
    stopped: list[float] = []
    dsh.script([[_chunk("agent_message_chunk", "a", "partial"), {"wait_cancel": True}]])

    def on_event(frame):
        # Stop once the answer has begun, however long the agent took to start.
        if not cancel.is_set() and "partial" in json.dumps(frame):
            stopped.append(time.monotonic())
            cancel.set()

    req = _request()
    text, _, _ = DeepSeekHarnessEngine().run_turn(
        req=req, history=[], on_event=on_event, cancel_event=cancel,
        assistant_msg_id=f"{req.user_msg_id}_reply", ordered_blocks_out=[], execution_context=None)
    assert text == "partial" and time.monotonic() - stopped[0] < 5
    assert dsh.log("in", "session/cancel")


def test_effort_maps_to_the_nearest_level():
    from openprogram.runtime.harnesses.deepseek.engine import dsh_effort
    levels = ["off", "low", "high", "max"]
    assert dsh_effort("medium", levels) == "high"
    assert dsh_effort("xhigh", levels) == "max"
    assert dsh_effort("minimal", levels) == "low"
    assert dsh_effort("auto", levels) is None
