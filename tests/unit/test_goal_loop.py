"""Session-goal loop tests — /goal set/status/clear, the dispatcher
continuation loop, and every stop rule.

Two layers, mirroring the dispatcher's own test split:

* ``continue_goal_turns`` unit tests drive the loop with a fake
  ``run_turn`` (no agent loop at all) — cap / clear / judge-failure /
  idle-spin rules.
* One end-to-end test runs the REAL ``process_user_turn`` with a fake
  ``stream_fn`` (the dispatcher's established fake-LLM seam) and a
  ``--check`` predicate that flips from failing to passing, asserting
  the loop self-continues exactly once and lands on ``achieved``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

import openprogram.agent.goal as G
from openprogram.agent import dispatcher as D
from openprogram.agent.dispatcher.types import TurnRequest, TurnResult
from openprogram.agent.session_db import SessionDB
from openprogram.providers.types import (
    AssistantMessage,
    AssistantMessageEvent,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    Usage,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.store.session_store.default_store",
                        lambda: db)
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    return db


@pytest.fixture(autouse=True)
def captured_goal_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace the emit fan-out (which best-effort imports the webui
    server) with a collector — keeps the tests hermetic and lets them
    assert the emitted status sequence."""
    events: list[dict] = []

    def _collect(on_event, session_id, goal):
        events.append({"session_id": session_id, "goal": dict(goal)})

    monkeypatch.setattr(G, "_emit_goal_update", _collect)
    return events


@pytest.fixture(autouse=True)
def stub_model_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        D, "_resolve_model",
        lambda profile, override=None: Model(
            id="stub", name="stub", api="completion", provider="openai",
            base_url="https://api.openai.com/v1"))


@pytest.fixture(autouse=True)
def stub_agent_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        D, "_load_agent_profile",
        lambda agent_id: {"id": agent_id, "system_prompt": "you are helpful"})


def _set_goal(db: SessionDB, session_id: str, **overrides) -> dict:
    goal = {
        "text": "the goal", "check": "", "status": "active",
        "created_at": time.time(), "turns_used": 0, "max_turns": 20,
        "last_reason": "", "judge_parse_failures": 0,
    }
    goal.update(overrides)
    if db.get_session(session_id) is None:
        db.create_session(session_id, "main")
    db.update_session(session_id, goal=goal)
    return goal


def _req(session_id: str = "s1", source: str = "tui") -> TurnRequest:
    return TurnRequest(session_id=session_id, user_text="hi",
                       agent_id="main", source=source)


def _result(tools: bool = True) -> TurnResult:
    return TurnResult(
        final_text="ok", user_msg_id="u", assistant_msg_id="a",
        tool_calls=[{"tool": "bash"}] if tools else [])


# ---------------------------------------------------------------------------
# /goal command parsing + meta writes
# ---------------------------------------------------------------------------

def test_goal_set_status_clear(tmp_db: SessionDB) -> None:
    tmp_db.create_session("s1", "main")

    out = G.handle_goal_command("s1", 'tests pass --check "true"')
    assert out["send_text"] == "tests pass"
    goal = G.load_goal("s1")
    assert goal["status"] == "active"
    assert goal["check"] == "true"
    assert goal["text"] == "tests pass"
    assert goal["max_turns"] == 20

    status = G.handle_goal_command("s1", "")
    assert status["send_text"] is None
    assert "active" in status["text"] and "tests pass" in status["text"]

    for verb in ("clear", "stop", "off", "cancel"):
        _set_goal(tmp_db, "s1")
        out = G.handle_goal_command("s1", verb)
        assert out["send_text"] is None
        assert G.load_goal("s1")["status"] == "cleared"

    # Clearing with nothing active is a no-op message, not a crash.
    assert "No active goal" in G.handle_goal_command("s1", "clear")["text"]


def test_goal_set_check_only(tmp_db: SessionDB) -> None:
    tmp_db.create_session("s1", "main")
    out = G.handle_goal_command("s1", '--check "true"')
    goal = G.load_goal("s1")
    assert goal["check"] == "true"
    assert goal["text"].startswith("check passes:")
    assert out["send_text"] == goal["text"]


def test_goal_empty_set_is_usage(tmp_db: SessionDB) -> None:
    tmp_db.create_session("s1", "main")
    # No goal written yet + "--check" without value → usage text.
    out = G.handle_goal_command("s1", "--check")
    assert out["send_text"] is None
    assert "Usage" in out["text"]
    assert G.load_goal("s1") is None


# ---------------------------------------------------------------------------
# Loop unit tests (fake run_turn)
# ---------------------------------------------------------------------------

def test_check_predicate_flips_to_achieved(tmp_db: SessionDB, tmp_path: Path,
                                           captured_goal_events) -> None:
    """Turn 1 fails the predicate → one continuation runs → predicate
    passes → achieved."""
    flag = tmp_path / "done.flag"
    _set_goal(tmp_db, "s1", check=f"test -f {flag}")

    continuations: list[TurnRequest] = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        continuations.append(req)
        flag.write_text("done")          # the continuation "does the work"
        return _result(tools=True)

    final = G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    assert final.final_text == "ok"
    assert len(continuations) == 1
    cont = continuations[0]
    assert cont.source == "goal_continue"
    assert cont.user_text.startswith("[goal] 未达成：")
    assert cont.user_msg_id is None and cont.user_already_persisted is False

    goal = G.load_goal("s1")
    assert goal["status"] == "achieved"
    assert goal["turns_used"] == 2
    assert captured_goal_events[-1]["goal"]["status"] == "achieved"


def test_max_turns_caps_the_loop(tmp_db: SessionDB) -> None:
    _set_goal(tmp_db, "s1", check="false", max_turns=2)
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "capped"
    assert goal["turns_used"] == 2
    assert len(calls) == 1  # turn 2 ran, turn 3 was never launched


def test_clear_mid_loop_stops_continuation(tmp_db: SessionDB) -> None:
    _set_goal(tmp_db, "s1", check="false")
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        # Another surface issues /goal clear while the turn runs.
        G.handle_goal_command("s1", "clear")
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    assert len(calls) == 1
    assert G.load_goal("s1")["status"] == "cleared"


def test_judge_failures_three_strikes_error(tmp_db: SessionDB,
                                            monkeypatch) -> None:
    _set_goal(tmp_db, "s1", check="")   # no predicate → LLM judge
    monkeypatch.setattr(
        G, "_judge_llm",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llm down")))
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "error"
    assert goal["judge_parse_failures"] == 3
    assert "judge failed 3 times" in goal["last_reason"]
    assert len(calls) == 2  # failures 1 and 2 still continued


def test_zero_tool_continuation_is_idle_error(tmp_db: SessionDB) -> None:
    _set_goal(tmp_db, "s1", check="false")
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=False)      # continuation does nothing

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "error"
    assert "no tool calls" in goal["last_reason"]
    assert len(calls) == 1


def test_no_goal_is_a_passthrough(tmp_db: SessionDB) -> None:
    tmp_db.create_session("s1", "main")

    def run_turn(req, *, on_event=None, cancel_event=None):  # pragma: no cover
        raise AssertionError("must not launch a continuation")

    first = _result()
    assert G.continue_goal_turns(_req(), first, run_turn=run_turn) is first


def test_failed_turn_stops_loop(tmp_db: SessionDB) -> None:
    _set_goal(tmp_db, "s1", check="false")
    failed = TurnResult(final_text="", user_msg_id="u", assistant_msg_id="a",
                       failed=True, error="boom")

    def run_turn(req, *, on_event=None, cancel_event=None):  # pragma: no cover
        raise AssertionError("must not continue after a failed turn")

    assert G.continue_goal_turns(_req(), failed, run_turn=run_turn) is failed
    assert G.load_goal("s1")["status"] == "active"  # goal survives for retry


# ---------------------------------------------------------------------------
# Judge parsing
# ---------------------------------------------------------------------------

def test_judge_retries_once_then_parses(tmp_db: SessionDB, monkeypatch) -> None:
    _set_goal(tmp_db, "s1")
    replies = iter(["not json at all", '{"met": true, "reason": "done"}'])
    monkeypatch.setattr(G, "_judge_llm", lambda *a, **k: next(replies))
    verdict, reason, _question = G._evaluate_with_llm_judge(
        "s1", {"text": "the goal"}, agent_id="main", model_override=None)
    assert verdict == "met"
    assert reason == "done"


def test_judge_json_extraction() -> None:
    ok = G._parse_judge_json('```json\n{"met": false, "reason": "missing"}\n```')
    assert ok == (False, "missing", False, "")
    assert G._parse_judge_json("no braces here") is None
    assert G._parse_judge_json('{"met": "yes"}') is None  # met must be bool


# ---------------------------------------------------------------------------
# End-to-end through the real dispatcher (fake stream_fn)
# ---------------------------------------------------------------------------

def _make_text_stream_fn(chunks: list[str], on_call=None):
    full_text = "".join(chunks)

    def _partial(text: str = "") -> AssistantMessage:
        return AssistantMessage(
            content=[TextContent(text=text)] if text else [],
            api="completion", provider="openai", model="stub",
            timestamp=int(time.time() * 1000))

    async def _fn(model, context, options) -> AsyncGenerator[AssistantMessageEvent, None]:
        if on_call is not None:
            on_call()
        yield EventStart(partial=_partial(""))
        yield EventTextStart(content_index=0, partial=_partial(""))
        accum = ""
        for chunk in chunks:
            accum += chunk
            yield EventTextDelta(content_index=0, delta=chunk,
                                 partial=_partial(accum))
        yield EventTextEnd(content_index=0, content=accum,
                           partial=_partial(accum))
        yield EventDone(reason="stop", message=AssistantMessage(
            content=[TextContent(text=full_text)], api="completion",
            provider="openai", model="stub",
            usage=Usage(input_tokens=10, output_tokens=4),
            stop_reason="stop", timestamp=int(time.time() * 1000)))

    return _fn


# ---------------------------------------------------------------------------
# WebUI composer path: /goal typed into chat executes backend-side
# ---------------------------------------------------------------------------

class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        import json
        self.sent.append(json.loads(payload))


@pytest.fixture
def web_env(tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch):
    from openprogram.webui import server as srv
    srv._sessions.clear()
    srv._msg_cache.clear()
    return srv


def test_web_goal_status_and_clear_skip_the_turn(tmp_db: SessionDB, web_env,
                                                 monkeypatch) -> None:
    import asyncio
    from openprogram.webui.ws_actions.chat import handle_chat

    srv = web_env
    launched: list = []
    monkeypatch.setattr(srv, "_execute_in_context",
                        lambda *a, **k: launched.append(a))
    tmp_db.create_session("web1", "main")
    srv._get_or_create_session("web1", agent_id="main")

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {"text": "/goal", "session_id": "web1"}))
    local = [m for m in ws.sent
             if m.get("data", {}).get("type") == "local_command"]
    assert len(local) == 1
    assert "No goal set" in local[0]["data"]["content"]
    assert not [m for m in ws.sent if m["type"] == "chat_ack"]
    assert launched == []

    _set_goal(tmp_db, "web1")
    ws2 = _FakeWS()
    asyncio.run(handle_chat(ws2, {"text": "/goal clear", "session_id": "web1"}))
    assert G.load_goal("web1")["status"] == "cleared"
    assert launched == []


def test_web_goal_set_rewrites_text_and_starts_turn(tmp_db: SessionDB,
                                                    web_env,
                                                    monkeypatch) -> None:
    import asyncio
    import threading as _threading
    from openprogram.webui.ws_actions.chat import handle_chat

    srv = web_env
    started = _threading.Event()
    calls: list = []

    def _fake_execute(*a, **k):
        calls.append((a, k))
        started.set()

    monkeypatch.setattr(srv, "_execute_in_context", _fake_execute)
    tmp_db.create_session("web2", "main")
    srv._get_or_create_session("web2", agent_id="main")

    ws = _FakeWS()
    asyncio.run(handle_chat(
        ws, {"text": '/goal --check "true" tests pass',
             "session_id": "web2"}))
    assert started.wait(5), "turn thread never launched"

    goal = G.load_goal("web2")
    assert goal["status"] == "active" and goal["check"] == "true"
    # The persisted user message is the goal DIRECTIVE, not "/goal …".
    msgs = tmp_db.get_messages("web2")
    user_rows = [m for m in msgs if m["role"] == "user"]
    assert user_rows and user_rows[-1]["content"] == "tests pass"
    # Confirmation went out, and the normal ack/turn flow followed.
    local = [m for m in ws.sent
             if m.get("data", {}).get("type") == "local_command"]
    assert len(local) == 1 and "Goal set" in local[0]["data"]["content"]
    assert [m for m in ws.sent if m["type"] == "chat_ack"]
    assert calls and calls[0][0][2] == "query"


def test_dispatcher_end_to_end_goal_achieved(tmp_db: SessionDB,
                                             tmp_path: Path,
                                             monkeypatch) -> None:
    """Real process_user_turn: turn 1 leaves the predicate failing, the
    goal loop launches ONE continuation turn (a first-class turn — its
    own user+assistant rows), the second turn flips the predicate, and
    the goal ends achieved."""
    flag = tmp_path / "made-by-turn-2.flag"
    _set_goal(tmp_db, "e2e", check=f"test -f {flag}")

    turn_count = {"n": 0}

    def _on_stream_call():
        turn_count["n"] += 1
        if turn_count["n"] >= 2:
            flag.write_text("done")

    fake_stream = _make_text_stream_fn(["work ", "done"],
                                       on_call=_on_stream_call)
    # Idle-spin guard: text-only fake turns make zero tool calls, which
    # would (correctly) stop an unmet continuation. Give the loop tool
    # activity by patching the result the guard reads is unnecessary —
    # the second turn ACHIEVES the goal, and "met" wins before the idle
    # check, exercising exactly that ordering.
    orig = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None,
                 **extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake_stream, **extra)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        result = D.process_user_turn(
            TurnRequest(session_id="e2e", user_text="start",
                        agent_id="main", source="tui"))

    assert result.failed is False
    assert turn_count["n"] == 2

    goal = G.load_goal("e2e")
    assert goal["status"] == "achieved"
    assert goal["turns_used"] == 2

    msgs = tmp_db.get_branch("e2e")
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert msgs[2]["content"].startswith("[goal] 未达成：")
    assert msgs[2]["source"] == "goal_continue"


# ---------------------------------------------------------------------------
# needs_user: the verification step pauses the loop for the user
# ---------------------------------------------------------------------------

def test_needs_user_pauses_loop(tmp_db: SessionDB, monkeypatch) -> None:
    """A need_user verdict from the judge pauses the loop (no
    continuation launches), records the question, and a later real user
    turn resumes it."""
    _set_goal(tmp_db, "s1", check="")   # no predicate → LLM judge
    monkeypatch.setattr(
        G, "_judge_llm",
        lambda *a, **k: ('{"met": false, "reason": "direction unclear", '
                         '"need_user": true, "question": "用方案A还是方案B？"}'))
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "waiting_user"
    assert goal["last_question"] == "用方案A还是方案B？"
    assert calls == []          # nothing launched while waiting

    # A goal_continue turn is never the answer — still waiting.
    G.continue_goal_turns(_req(source="goal_continue"), _result(),
                          run_turn=run_turn)
    assert G.load_goal("s1")["status"] == "waiting_user"
    assert calls == []

    # A real user turn resumes: judge now says unmet → one continuation.
    answers = iter([
        '{"met": false, "reason": "keep going", "need_user": false, '
        '"question": ""}',
        '{"met": true, "reason": "done", "need_user": false, "question": ""}',
    ])
    monkeypatch.setattr(G, "_judge_llm", lambda *a, **k: next(answers))
    G.continue_goal_turns(_req(source="web"), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "achieved"
    assert "last_question" not in goal
    assert len(calls) == 1      # exactly the one resumed continuation


def test_needs_user_without_question_continues(tmp_db: SessionDB,
                                               monkeypatch) -> None:
    """need_user=true with an empty question is not actionable — the
    loop treats it as unmet and keeps going."""
    _set_goal(tmp_db, "s1", check="", max_turns=1)
    monkeypatch.setattr(
        G, "_judge_llm",
        lambda *a, **k: ('{"met": false, "reason": "r", "need_user": true, '
                         '"question": ""}'))
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    assert G.load_goal("s1")["status"] == "capped"


def test_clear_covers_waiting_user(tmp_db: SessionDB) -> None:
    _set_goal(tmp_db, "s1", status="waiting_user")
    out = G.handle_goal_command("s1", "clear")
    assert "cleared" in G.load_goal("s1")["status"]
    assert out["send_text"] is None


# ---------------------------------------------------------------------------
# Active verification of stop verdicts
# ---------------------------------------------------------------------------

def test_met_verdict_is_actively_verified(tmp_db: SessionDB,
                                          monkeypatch) -> None:
    """A met verdict from the tail judge only stops the loop after the
    verifier confirms it from the working directory; a refuted claim
    continues with the verifier's gap as the reason."""
    _set_goal(tmp_db, "s1", check="")
    monkeypatch.setattr(
        G, "_judge_llm",
        lambda *a, **k: '{"met": true, "reason": "looks done"}')
    verify_replies = iter([
        '{"confirmed": false, "evidence": "3 tests fail", "gap": "测试没过"}',
        '{"confirmed": true, "evidence": "全部测试通过", "gap": ""}',
    ])
    prompts = []

    def fake_verifier(session_id, prompt, *, agent_id, spawn_caller):
        prompts.append(prompt)
        return next(verify_replies)

    monkeypatch.setattr(G, "_run_verifier_turn", fake_verifier)
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "achieved"
    assert goal["last_reason"] == "全部测试通过"
    assert len(calls) == 1                       # one refuted → one more turn
    assert "核实未通过：测试没过" in calls[0].user_text
    assert len(prompts) == 2
    assert "不要相信" in prompts[0]


def test_needs_user_refuted_keeps_running(tmp_db: SessionDB,
                                          monkeypatch) -> None:
    """A needs_user claim the verifier refutes does not pause — the loop
    continues with the gap."""
    _set_goal(tmp_db, "s1", check="", max_turns=1)
    monkeypatch.setattr(
        G, "_judge_llm",
        lambda *a, **k: ('{"met": false, "reason": "r", "need_user": true, '
                         '"question": "缺凭据吗？"}'))
    monkeypatch.setattr(
        G, "_run_verifier_turn",
        lambda *a, **k: '{"confirmed": false, "evidence": "", '
                        '"gap": "凭据其实在环境变量里"}')
    G.continue_goal_turns(_req(), _result(), run_turn=lambda req, **k: _result())
    goal = G.load_goal("s1")
    assert goal["status"] == "capped"            # continued, hit max_turns=1
    assert "last_question" not in goal


def test_verifier_failure_trusts_cheap_verdict(tmp_db: SessionDB,
                                               monkeypatch) -> None:
    _set_goal(tmp_db, "s1", check="")
    monkeypatch.setattr(
        G, "_judge_llm",
        lambda *a, **k: '{"met": true, "reason": "done"}')
    monkeypatch.setattr(
        G, "_run_verifier_turn",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spawn down")))
    G.continue_goal_turns(_req(), _result(), run_turn=lambda req, **k: _result())
    assert G.load_goal("s1")["status"] == "achieved"


def test_check_goals_skip_active_verification(tmp_db: SessionDB,
                                              monkeypatch) -> None:
    _set_goal(tmp_db, "s1", check="true")
    called = []
    monkeypatch.setattr(
        G, "_run_verifier_turn",
        lambda *a, **k: called.append(1) or '{"confirmed": true}')
    G.continue_goal_turns(_req(), _result(), run_turn=lambda req, **k: _result())
    assert G.load_goal("s1")["status"] == "achieved"
    assert called == []                          # the command IS the evidence
