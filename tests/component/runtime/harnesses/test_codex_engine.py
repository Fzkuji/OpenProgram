"""A Codex turn against a stand-in ``codex app-server``: frames, cards, approvals, Stop, resume, crashes."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

FAKE = Path(__file__).with_name("fake_codex_app_server.py")


@pytest.fixture
def codex(tmp_path, monkeypatch):
    from openprogram.runtime.harnesses.codex import client
    import openprogram.runtime.context.session_db as session_db
    import openprogram.state.store.session.session_store as session_store_module
    from openprogram.state.store.session.session_store import SessionStore

    binary = tmp_path / "codex"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    binary.chmod(0o755)
    log = tmp_path / "codex.log"
    scenario = tmp_path / "scenario.json"
    monkeypatch.setenv("OPENPROGRAM_CODEX_BINARY", str(binary))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", str(scenario))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "turns"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    pool = client.ProcessPool()
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

    harness = SimpleNamespace(tmp=tmp_path, log=lines, scenario=scenario, sessions=sessions,
                              pool=pool, workdir=workdir)
    harness.script = lambda turns: scenario.write_text(json.dumps(turns))
    try:
        yield harness
    finally:
        pool.close_all()


def _request(text="hello", *, msg="m1", mode="bypass", effort="high", branch=None):
    return SimpleNamespace(
        session_id="s1", user_msg_id=msg, user_text=text, model_override="codex-harness/gpt-fake",
        permission_mode=mode, thinking_effort=effort, attachments=None, branch_id=branch,
        agent_id="main", source="web",
    )


def _run(req, *, history=None, cancel=None, context=None):
    from openprogram.runtime.harnesses.codex.engine import CodexEngine

    frames: list[dict] = []
    blocks: list[dict] = []
    result = CodexEngine().run_turn(
        req=req, history=history or [], on_event=frames.append, cancel_event=cancel or threading.Event(),
        assistant_msg_id=f"{req.user_msg_id}_reply", ordered_blocks_out=blocks, execution_context=context,
    )
    return result, frames, blocks


def _events(frames):
    return [f["data"]["event"] for f in frames if f["data"].get("type") == "stream_event"]


def _item(kind, item_id, **fields):
    return {"type": kind, "id": item_id, **fields}


STREAM = [
    {"send": {"method": "item/started", "params": {"item": _item("reasoning", "r1")}}},
    {"send": {"method": "item/reasoning/summaryTextDelta", "params": {"itemId": "r1", "delta": "Thinking"}}},
    {"send": {"method": "item/completed", "params": {"item": _item("reasoning", "r1", summary=["Thinking"])}}},
    {"send": {"method": "item/agentMessage/delta", "params": {"itemId": "a1", "delta": "Let me look."}}},
    {"send": {"method": "item/started", "params": {"item": _item(
        "commandExecution", "c1", command="/bin/zsh -lc ls", cwd="/w", status="inProgress")}}},
    {"send": {"method": "item/completed", "params": {"item": _item(
        "commandExecution", "c1", command="/bin/zsh -lc ls", cwd="/w", aggregatedOutput="a.txt\n",
        exitCode=0, status="completed")}}},
    {"send": {"method": "item/started", "params": {"item": _item("fileChange", "f1", status="inProgress", changes=[
        {"path": "/w/a.txt", "kind": {"type": "update"}, "diff": "@@ -1 +1 @@\n-old\n+new\n"},
        {"path": "/w/b.txt", "kind": {"type": "add"}, "diff": "hi\n"}])}}},
    {"send": {"method": "item/completed", "params": {"item": _item("fileChange", "f1", status="completed", changes=[
        {"path": "/w/a.txt", "kind": {"type": "update"}, "diff": "@@ -1 +1 @@\n-old\n+new\n"},
        {"path": "/w/b.txt", "kind": {"type": "add"}, "diff": "hi\n"}])}}},
    {"send": {"method": "item/completed", "params": {"item": _item(
        "webSearch", "w1", query="codex docs", action={"type": "search", "query": "codex docs"})}}},
    {"send": {"method": "item/started", "params": {"item": _item(
        "mcpToolCall", "x1", server="docs", tool="lookup", arguments={"q": 1}, status="inProgress")}}},
    {"send": {"method": "item/completed", "params": {"item": _item(
        "mcpToolCall", "x1", server="docs", tool="lookup", arguments={"q": 1}, status="completed",
        result={"content": [{"type": "text", "text": "found"}]})}}},
    {"send": {"method": "turn/plan/updated", "params": {"turnId": "$TURN", "plan": [
        {"step": "Read", "status": "completed"}, {"step": "Write", "status": "inProgress"}]}}},
    {"send": {"method": "thread/tokenUsage/updated", "params": {"threadId": "$THREAD", "turnId": "$TURN", "tokenUsage": {
        "last": {"inputTokens": 1000, "cachedInputTokens": 600, "cacheWriteInputTokens": 0, "outputTokens": 50,
                 "reasoningOutputTokens": 10, "totalTokens": 1050},
        "total": {"inputTokens": 1000, "cachedInputTokens": 600, "cacheWriteInputTokens": 0, "outputTokens": 50,
                  "reasoningOutputTokens": 10, "totalTokens": 1050},
        "modelContextWindow": 400000}}}},
    {"send": {"method": "item/agentMessage/delta", "params": {"itemId": "a2", "delta": "Done."}}},
    {"send": {"method": "item/completed", "params": {"item": _item("agentMessage", "a2", text="Done.")}}},
]


def test_a_turn_streams_text_reasoning_and_cards(codex):
    codex.script([STREAM])
    (text, usage, calls), frames, blocks = _run(_request(mode="acceptEdits"))

    events = _events(frames)
    assert {"type": "thinking", "text": "Thinking"} in events
    assert [e["text"] for e in events if e["type"] == "text"] == ["Let me look.", "Done."]
    uses = {e["tool_call_id"]: e for e in events if e["type"] == "tool_use"}
    assert json.loads(uses["c1"]["input"]) == {"command": "ls", "workdir": "/w"}
    patch = json.loads(uses["f1"]["input"])["input"]
    assert patch.splitlines() == ["*** Begin Patch", "*** Update File: /w/a.txt", "@@ -1 +1 @@", "-old", "+new",
                                  "*** Add File: /w/b.txt", "+hi", "*** End Patch"]
    assert uses["x1"]["tool"] == "mcp__docs__lookup"
    results = {e["tool_call_id"]: e for e in events if e["type"] == "tool_result"}
    assert results["c1"]["result"] == "a.txt\n" and results["c1"]["outcome"] == "completed"
    assert results["w1"]["provider_executed"] is True and results["w1"]["tool"] == "web_search"
    assert results["x1"]["result"] == "found"
    plan = next(e for e in events if e["type"] == "tool_use" and e["tool"] == "todo_write")
    assert json.loads(plan["input"])["todos"][1] == {"content": "Write", "status": "in_progress"}

    assert text == "Let me look.\n\nDone."
    assert [b["type"] for b in blocks] == ["thinking", "text", "tool", "tool", "tool", "tool", "tool", "text"]
    assert blocks[0]["round_start"] and blocks[-1]["round_start"]
    assert {c["tool"] for c in calls} == {"bash", "apply_patch", "mcp__docs__lookup", "todo_write"}
    assert usage["input_tokens"] == 400 and usage["cache_read_tokens"] == 600 and usage["output_tokens"] == 50
    assert usage["provider_request_count"] == 1 and usage["context_tokens"] == 1000
    report = next(f["data"] for f in frames if f["data"].get("type") == "context_usage")
    assert report["model"] == "codex-harness/gpt-fake" and report["context_window"] == 400000

    start = codex.log("in", "thread/start")[0]["params"]
    assert start["approvalPolicy"] == "on-request" and start["sandbox"] == "workspace-write"
    assert start["cwd"] == str(codex.workdir) and "developerInstructions" not in start
    turn = codex.log("in", "turn/start")[0]["params"]
    assert turn["effort"] == "high" and turn["model"] == "gpt-fake"
    assert turn["sandboxPolicy"] == {"type": "workspaceWrite"}
    assert turn["input"] == [{"type": "text", "text": "hello"}]
    init = codex.log("in", "initialize")[0]["params"]
    assert init["clientInfo"]["name"] == "OpenProgram"
    record = codex.sessions.get_session("s1")["harness_threads"]["codex-harness"]["main"]
    assert record["thread_id"].startswith("thread-") and record["seen"] == "m1_reply"


def test_the_next_turn_resumes_the_thread_only_in_a_new_process(codex):
    codex.script([[], [], []])
    _run(_request(msg="m1"))
    thread = codex.sessions.get_session("s1")["harness_threads"]["codex-harness"]["main"]["thread_id"]
    history = [{"id": "m1", "role": "user", "content": "hello"}, {"id": "m1_reply", "role": "assistant", "content": ""}]
    _run(_request(msg="m2"), history=history)
    assert codex.log("in", "thread/resume") == []
    codex.pool.close_all()
    history += [{"id": "m2", "role": "user", "content": "x"}, {"id": "m2_reply", "role": "assistant", "content": ""}]
    _run(_request(msg="m3"), history=history)
    resumed = codex.log("in", "thread/resume")
    assert [r["params"]["threadId"] for r in resumed] == [thread]
    assert all(row["params"]["threadId"] == thread for row in codex.log("in", "turn/start"))


def test_turns_the_thread_missed_reach_it_as_a_handoff(codex):
    codex.script([[], []])
    history = [{"id": "u0", "role": "user", "content": "What is 2+2?"},
               {"id": "u0_reply", "role": "assistant", "content": "4"}]
    _run(_request(msg="m1"), history=history)
    instructions = codex.log("in", "thread/start")[0]["params"]["developerInstructions"]
    assert "User: What is 2+2?" in instructions and "Assistant: 4" in instructions
    # An OpenProgram turn ran in between: the next Codex turn is told.
    history += [{"id": "m1", "role": "user", "content": "hello"}, {"id": "m1_reply", "role": "assistant", "content": ""},
                {"id": "o1", "role": "user", "content": "Asked elsewhere"},
                {"id": "o1_reply", "role": "assistant", "content": "Answered by OpenProgram"}]
    _run(_request(msg="m2"), history=history)
    inputs = codex.log("in", "turn/start")[1]["params"]["input"]
    assert len(inputs) == 2 and "Answered by OpenProgram" in inputs[0]["text"]
    assert "hello" not in inputs[0]["text"]


def test_a_new_branch_forks_the_thread_at_its_latest_turn(codex):
    codex.script([[], []])
    _run(_request(msg="m1"))
    history = [{"id": "m1", "role": "user", "content": "hello"}, {"id": "m1_reply", "role": "assistant", "content": ""}]
    _run(_request(msg="m2", branch="b2"), history=history)
    forked = codex.log("in", "thread/fork")
    assert len(forked) == 1 and codex.log("in", "thread/start").__len__() == 1


APPROVAL = [
    {"send": {"method": "item/started", "params": {"item": _item(
        "commandExecution", "c1", command="/bin/zsh -lc 'rm -rf build'", cwd="/w", status="inProgress")}}},
    {"request": {"method": "item/commandExecution/requestApproval", "params": {
        "threadId": "$THREAD", "turnId": "$TURN", "itemId": "c1", "command": "/bin/zsh -lc 'rm -rf build'",
        "cwd": "/w", "reason": "needs write"}}},
    {"send": {"method": "item/completed", "params": {"item": _item(
        "commandExecution", "c1", command="rm -rf build", status="declined")}}},
]


@pytest.mark.parametrize("outcome,scope,decision", [
    ("allow", "once", "accept"), ("allow", "session", "acceptForSession"), ("deny", "once", "decline"),
])
def test_an_approval_request_waits_for_the_person(codex, monkeypatch, outcome, scope, decision):
    from openprogram.runtime.permissions.approvals import live

    asked = []

    def ask(owner, **kwargs):
        asked.append(kwargs)
        return live.LiveDecision(outcome, scope)

    monkeypatch.setattr(live, "ask_live_approval", ask)
    monkeypatch.setattr(live, "live_owner_from", lambda *_a: object())
    codex.script([APPROVAL])
    _run(_request(mode="ask"))
    assert asked[0]["tool"] == "bash" and asked[0]["arguments"] == {"command": "rm -rf build", "workdir": "/w"}
    assert asked[0]["detail"] == "needs write"
    answer = next(row for row in codex.log("in") if row.get("id") == 1000)
    assert answer["result"] == {"decision": decision}
    start = codex.log("in", "thread/start")[0]["params"]
    assert start["approvalPolicy"] == "untrusted" and start["sandbox"] == "read-only"


def test_codex_requests_nobody_takes_are_refused(codex):
    codex.script([[
        {"request": {"method": "item/commandExecution/requestApproval", "params": {"itemId": "c9", "command": "ls"}}},
        {"request": {"method": "item/tool/requestUserInput", "params": {"itemId": "q", "questions": []}}},
        {"request": {"method": "mcpServer/elicitation/request", "params": {}}},
        {"request": {"method": "something/new", "params": {}}},
    ]])
    _run(_request())
    answers = {row["id"]: row for row in codex.log("in") if isinstance(row.get("id"), int) and row["id"] >= 1000}
    assert answers[1000]["result"] == {"decision": "decline"}
    assert answers[1001]["result"] == {"answers": {}}
    assert answers[1002]["result"] == {"action": "decline"}
    assert answers[1003]["error"]["code"] == -32601


def test_stop_interrupts_the_turn(codex):
    codex.script([[
        {"send": {"method": "item/agentMessage/delta", "params": {"itemId": "a1", "delta": "Working"}}},
        {"send": {"method": "item/started", "params": {"item": _item("commandExecution", "c1", command="sleep 60")}}},
        {"wait_for": "turn/interrupt"},
        {"complete": "interrupted"},
    ]])
    cancel = threading.Event()
    req = _request()
    frames: list[dict] = []

    def on_event(frame):
        frames.append(frame)
        if frame["data"].get("event", {}).get("type") == "tool_use":
            cancel.set()

    from openprogram.runtime.harnesses.codex.engine import CodexEngine
    blocks: list[dict] = []
    text, _usage, calls = CodexEngine().run_turn(
        req=req, history=[], on_event=on_event, cancel_event=cancel, assistant_msg_id="m1_reply",
        ordered_blocks_out=blocks, execution_context=None)
    assert codex.log("in", "turn/interrupt")[0]["params"]["turnId"] == "turn-0"
    assert text == "Working"
    assert calls[0]["outcome"] == "cancelled"


def test_a_stop_codex_ignores_ends_its_process(codex, monkeypatch):
    from openprogram.runtime.harnesses.codex import engine
    monkeypatch.setattr(engine, "INTERRUPT_GRACE_SECONDS", 0.5)
    codex.script([[{"hang": True}]])
    cancel = threading.Event()

    def stop_once_started() -> None:
        # Stop after Codex has the turn: a Stop before turn/start is the queued case below.
        deadline = time.monotonic() + 20
        while not codex.log("in", "turn/start") and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
        cancel.set()

    threading.Thread(target=stop_once_started, daemon=True).start()
    started = time.monotonic()
    _run(_request(), cancel=cancel)
    assert time.monotonic() - started < 20
    assert codex.pool._entries == {}


def test_a_stop_before_the_turn_has_an_id_still_stops_it(codex):
    codex.script([[{"wait_for": "turn/interrupt"}, {"complete": "interrupted"}]])
    cancel = threading.Event()
    cancel.set()
    (text, _usage, _calls), _frames, _blocks = _run(_request(), cancel=cancel)
    assert codex.log("in", "turn/start") == [] and text == ""


def test_a_crash_fails_the_turn_and_the_next_one_resumes(codex):
    from openprogram.models.providers.utils.failures.errors import LLMError

    codex.script([[], [{"exit": 3}], []])
    _run(_request(msg="m1"))
    thread = codex.sessions.get_session("s1")["harness_threads"]["codex-harness"]["main"]["thread_id"]
    history = [{"id": "m1", "role": "user", "content": "hello"}, {"id": "m1_reply", "role": "assistant", "content": ""}]
    with pytest.raises(LLMError, match="Codex stopped unexpectedly"):
        _run(_request(msg="m2"), history=history)
    _run(_request(msg="m3"), history=history)
    assert [row["params"]["threadId"] for row in codex.log("in", "thread/resume")] == [thread]


def test_a_failed_turn_says_why(codex):
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError

    codex.script([[{"complete": "failed", "error": {"message": "limit", "codexErrorInfo": "usageLimitExceeded"}}]])
    with pytest.raises(LLMError) as raised:
        _run(_request())
    assert raised.value.reason is ErrorReason.RATE_LIMIT
    assert "usage limit" in raised.value.message


def test_steering_goes_to_the_running_turn(codex, monkeypatch):
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    queue = [{"command_id": "cmd1", "payload": {"message": "also check tests"}}]
    hook = SimpleNamespace(take_open_steers=lambda: None)

    def source(req, **_kwargs):
        delivered = {}
        message = SimpleNamespace()

        async def take():
            item = queue.pop(0)
            delivered[id(message)] = {"type": "user_message", "msg_id": "steer1", "content": item["payload"]["message"],
                                      "timestamp": 1.0, "command_id": item["command_id"], "steering": True}
            return [message]
        return take, delivered

    monkeypatch.setattr(loop_runner, "_steering_source", source)
    codex.script([[{"wait_for": "turn/steer"}]])
    (_text, _usage, _calls), frames, blocks = _run(
        _request(), context={"safe_point_hook": hook, "steer_inputs": queue, "canonical_execution": True})
    steer = codex.log("in", "turn/steer")[0]["params"]
    assert steer["expectedTurnId"] == "turn-0" and steer["input"] == [{"type": "text", "text": "also check tests"}]
    assert any(f["data"].get("type") == "user_message" for f in frames)
    assert blocks[0]["type"] == "steering"


def test_the_listing_reads_models_and_sign_in(codex, monkeypatch):
    from openprogram.runtime.harnesses import models
    from openprogram.runtime.harnesses.codex import maintenance

    monkeypatch.setattr(maintenance, "latest_version", lambda _kind: "10.0.0")
    monkeypatch.setattr(maintenance, "manifest_models", lambda: [{"slug": "gpt-next", "name": "GPT Next"}])
    models.forget()
    entry = models.read_codex()
    assert entry["installed"] and entry["version"] == "9.9.9" and entry["signedIn"] and entry["plan"] == "plus"
    assert entry["models"] == [{"id": "gpt-fake", "label": "GPT Fake", "default": True,
                                "efforts": ["low", "medium", "high"], "default_effort": "medium", "images": True,
                                "personality": True}]
    # In place of an Agent: Codex's default, its config.toml profiles, its personalities.
    assert [agent["id"] for agent in entry["agents"]] == [
        "default", "profile:fast", "personality:pragmatic", "personality:friendly", "personality:none"]
    assert "someone@example.com" not in json.dumps(entry)
    # A newer Codex is out: the models only it lists are named, and the update is not ours to run here.
    assert entry["latest"] == "10.0.0" and entry["newModels"] == [{"id": "gpt-next", "label": "GPT Next"}]
    assert entry["updatable"] is False


def test_the_listing_says_when_codex_is_missing(codex, monkeypatch, tmp_path):
    from openprogram.runtime.harnesses import models

    monkeypatch.setenv("OPENPROGRAM_CODEX_BINARY", str(tmp_path / "missing"))
    assert models.read_codex()["installed"] is False


@pytest.mark.parametrize("choice, listed, launch, param", [
    ("profile:fast", False, ["-c", 'profile="fast"'], None),
    ("personality:friendly", True, [], "friendly"),
    ("personality:friendly", False, [], None),
    (None, True, [], None),
])
def test_the_branch_agent_choice_reaches_codex(codex, monkeypatch, choice, listed, launch, param):
    """A profile is a launch argument; a personality goes on thread/start and
    turn/start, only for a model Codex says takes one."""
    from openprogram.runtime.harnesses import models
    codex.sessions.update_session("s1", harness="codex", harness_agent=choice)
    monkeypatch.setattr(models, "harness_model", lambda _p, _m: {"id": "gpt-fake", "efforts": [], "personality": listed})
    codex.script([[{"send": {"method": "item/agentMessage/delta", "params": {"itemId": "a1", "delta": "ok"}}}]])
    _run(_request())
    assert codex.log("start")[0]["argv"] == ["app-server", *launch]
    for method in ("thread/start", "turn/start"):
        sent = codex.log("in", method)[0]["params"]
        assert sent.get("personality") == param
