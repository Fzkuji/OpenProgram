"""Session-goal loop tests — /goal set/status/clear, the dispatcher
continuation loop, and every stop rule.

Two layers, mirroring the dispatcher's own test split:

* ``continue_goal_turns`` unit tests drive the loop with a fake
  ``run_turn`` (no agent loop at all) — cap / clear / judge-failure /
  idle-spin rules.
* One end-to-end test runs the REAL ``process_user_turn`` with a fake
  ``stream_fn`` (the dispatcher's established fake-LLM seam) and a
  judge verdict that flips from unmet to met, asserting the loop
  self-continues exactly once and lands on ``achieved``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

import openprogram.programs.workflow.goal as G
import openprogram.programs.workflow.goal.verification as GF

# The autouse fixture replaces G._emit_goal_update with a collector;
# keep a module-import-time reference so the payload-shape test can
# still exercise the real function.
_REAL_EMIT_GOAL_UPDATE = G._emit_goal_update
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
    monkeypatch.setattr("openprogram.store.session.session_store.default_store",
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
def captured_spec_refinements(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the background spec-refinement kickoff (which would
    spawn a real agent turn on a thread) with a recorder."""
    started: list[str] = []
    monkeypatch.setattr(G, "_start_spec_refinement", started.append)
    return started


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
        "text": "the goal", "status": "active",
        "created_at": time.time(), "turns_used": 0, "max_turns": None,
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

    out = G.handle_goal_command("s1", "tests pass")
    assert out["send_text"] == "tests pass"
    goal = G.load_goal("s1")
    assert goal["status"] == "active"
    assert goal["text"] == "tests pass"
    assert goal["max_turns"] is None    # unlimited by default

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


def test_goal_set_starts_spec_refinement(tmp_db: SessionDB,
                                         captured_spec_refinements) -> None:
    tmp_db.create_session("s1", "main")
    G.handle_goal_command("s1", "tests pass")
    assert captured_spec_refinements == ["s1"]
    # The raw text is stored untouched; no spec until refinement lands.
    goal = G.load_goal("s1")
    assert goal["text"] == "tests pass" and "spec" not in goal


# ---------------------------------------------------------------------------
# Spec refinement — refine_goal_spec (run synchronously in tests)
# ---------------------------------------------------------------------------

def test_refine_goal_spec_stores_spec(tmp_db: SessionDB, monkeypatch,
                                      captured_goal_events) -> None:
    _set_goal(tmp_db, "s1", text="tests pass")
    notices: list = []
    monkeypatch.setattr(
        G, "refine_goal_spec_candidate",
        lambda text, session_id="", **k: (f"SPEC({text})", ["a", "b"]))
    monkeypatch.setattr(
        G, "_emit_goal_spec_notice",
        lambda sid, spec, checklist=None: notices.append(
            (sid, spec, checklist)))

    G.refine_goal_spec("s1")
    goal = G.load_goal("s1")
    assert goal["spec"] == "SPEC(tests pass)"
    assert goal["text"] == "tests pass"          # original text untouched
    assert goal["status"] == "active"
    # The refinement checklist lands as {"text", "done"} items.
    assert goal["checklist"] == [{"text": "a", "done": False},
                                 {"text": "b", "done": False}]
    assert captured_goal_events[-1]["goal"]["spec"] == "SPEC(tests pass)"
    assert notices == [("s1", "SPEC(tests pass)", ["a", "b"])]


def test_refine_goal_spec_empty_checklist_stores_none(tmp_db: SessionDB,
                                                      monkeypatch) -> None:
    """A prose-fallback refinement (checklist []) leaves the goal
    without a checklist key — the judge sees no <checklist> block."""
    _set_goal(tmp_db, "s1", text="tests pass")
    monkeypatch.setattr(G, "refine_goal_spec_candidate",
                        lambda text, session_id="", **k: ("SPEC", []))
    monkeypatch.setattr(G, "_emit_goal_spec_notice", lambda *a, **k: None)
    G.refine_goal_spec("s1")
    goal = G.load_goal("s1")
    assert goal["spec"] == "SPEC" and "checklist" not in goal


def test_refine_goal_spec_failure_fails_open(tmp_db: SessionDB,
                                             monkeypatch) -> None:
    _set_goal(tmp_db, "s1")
    monkeypatch.setattr(
        G, "refine_goal_spec_candidate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spawn down")))
    G.refine_goal_spec("s1")                     # must not raise
    goal = G.load_goal("s1")
    assert "spec" not in goal and goal["status"] == "active"


def test_refine_goal_spec_respects_clear_race(tmp_db: SessionDB,
                                              monkeypatch) -> None:
    """A /goal clear landing while the refinement turn runs must not be
    overwritten with a spec for a dead goal."""
    _set_goal(tmp_db, "s1")

    def _refine_then_cleared(text, session_id="", **k):
        G.handle_goal_command("s1", "clear")
        return "SPEC", []

    monkeypatch.setattr(G, "refine_goal_spec_candidate", _refine_then_cleared)
    G.refine_goal_spec("s1")
    goal = G.load_goal("s1")
    assert goal["status"] == "cleared" and "spec" not in goal


def test_judge_prefers_spec_over_text(tmp_db: SessionDB, monkeypatch) -> None:
    goal = _set_goal(tmp_db, "s1", spec="THE-SPEC")
    seen: list = []

    def _fake_decision(**kwargs):
        seen.append(kwargs["goal"])
        return {"met": True, "reason": "done", "need_user": False,
                "question": ""}

    monkeypatch.setattr(G, "judge_goal", _fake_decision)
    verdict, _, _, _ = G.evaluate_goal("s1", goal, agent_id="main")
    assert verdict == "met"
    assert seen == ["THE-SPEC"]

    # No spec → falls back to the raw text.
    goal2 = _set_goal(tmp_db, "s2", text="raw text")
    G.evaluate_goal("s2", goal2, agent_id="main")
    assert seen[-1] == "raw text"


# ---------------------------------------------------------------------------
# Checklist — per-item ticks, code-level met enforcement, continuation
# call-out
# ---------------------------------------------------------------------------

def test_terminal_status_says_why_in_the_transcript(
        tmp_db: SessionDB, monkeypatch) -> None:
    """A guard-stopped run must not just go quiet: the reason already
    written to goal state is also emitted as a transcript row."""
    _set_goal(tmp_db, "s1", checklist=[
        {"text": "a", "done": True}, {"text": "b", "done": False}])
    monkeypatch.setattr(
        G, "evaluate_goal",
        lambda sid, goal, *, agent_id, spawn_caller=None:
            ("unmet", "still 1/2", "", []))
    rows: list = []
    monkeypatch.setattr(
        G, "_emit_goal_notice",
        lambda sid, content, on_event=None: rows.append(content))

    G.continue_goal_turns(
        _req(source="goal_continue"), _result(),
        run_turn=lambda req, on_event=None, cancel_event=None: _result())

    assert G.load_goal("s1")["status"] == "error"
    assert any("已终止" in r and "stuck at 1/2" in r for r in rows), rows


def test_waiting_user_emits_only_its_question(
        tmp_db: SessionDB, monkeypatch) -> None:
    """A pause is not a terminal status — it gets the question row, not
    a "goal stopped" row on top of it."""
    _set_goal(tmp_db, "s1")
    monkeypatch.setattr(
        G, "evaluate_goal",
        lambda sid, goal, *, agent_id, spawn_caller=None:
            ("needs_user", "need a decision", "which one?", []))
    rows: list = []
    monkeypatch.setattr(
        G, "_emit_goal_notice",
        lambda sid, content, on_event=None: rows.append(content))

    G.continue_goal_turns(
        _req(source="goal_continue"), _result(),
        run_turn=lambda req, on_event=None, cancel_event=None: _result())

    assert G.load_goal("s1")["status"] == "waiting_user"
    assert rows == ["[goal] 需要你的确认才能继续：which one?"]


def test_checklist_stall_stops_loop(tmp_db: SessionDB, monkeypatch) -> None:
    """Read-only spin: continuation turns that call tools but never
    advance the checklist stop after STALL_ROUND_LIMIT flat rounds."""
    _set_goal(tmp_db, "s1", checklist=[
        {"text": "a", "done": True}, {"text": "b", "done": False}])
    monkeypatch.setattr(
        G, "evaluate_goal",
        lambda sid, goal, *, agent_id, spawn_caller=None:
            ("unmet", "still 1/2", "", []))

    G.continue_goal_turns(
        _req(source="goal_continue"), _result(),
        run_turn=lambda req, on_event=None, cancel_event=None: _result())

    stored = G.load_goal("s1")
    assert stored["status"] == "error"
    assert "stuck at 1/2" in stored["last_reason"]
    assert stored["stall_rounds"] == G.STALL_ROUND_LIMIT


def test_loop_write_preserves_refinement_landed_mid_evaluation(
        tmp_db: SessionDB, monkeypatch) -> None:
    """The background refinement lands spec/checklist WHILE the judge
    turn is running; the loop's progress write must not erase them with
    its pre-refinement snapshot (lost-update regression)."""
    _set_goal(tmp_db, "s1")  # no spec / checklist yet

    calls = {"n": 0}

    def _eval(session_id, goal, *, agent_id, spawn_caller=None):
        calls["n"] += 1
        if calls["n"] == 1:
            fresh = G.load_goal(session_id)
            fresh["spec"] = "refined spec"
            fresh["checklist"] = [{"text": "item", "done": False}]
            G.save_goal(session_id, fresh)
            return "unmet", "keep going", "", []
        return "met", "done", "", []

    monkeypatch.setattr(G, "evaluate_goal", _eval)
    G.continue_goal_turns(
        _req(), _result(),
        run_turn=lambda req, on_event=None, cancel_event=None: _result())

    stored = G.load_goal("s1")
    assert stored["spec"] == "refined spec"
    assert stored["checklist"] == [{"text": "item", "done": False}]
    assert stored["status"] == "achieved"


def test_checklist_flags_overwrite_done(tmp_db: SessionDB,
                                        monkeypatch) -> None:
    """The judge's equal-length bool list overwrites "done" in order —
    true→false included, evidence wins over an earlier tick."""
    goal = _set_goal(tmp_db, "s1", checklist=[
        {"text": "a", "done": True}, {"text": "b", "done": False}])
    monkeypatch.setattr(
        GF, "_run_decision_turn",
        lambda *a, **k: '{"met": false, "reason": "r", '
                        '"checklist": [false, true]}')
    verdict, _reason, _q, _o = G.evaluate_goal("s1", goal, agent_id="main")
    assert verdict == "unmet"
    assert goal["checklist"] == [{"text": "a", "done": False},
                                 {"text": "b", "done": True}]


def test_met_with_undone_checklist_downgrades(tmp_db: SessionDB,
                                              monkeypatch) -> None:
    """met from the judge while checklist items stay undone is forced
    down to unmet, and the reason names the undone items."""
    goal = _set_goal(tmp_db, "s1", checklist=[
        {"text": "tests green", "done": False},
        {"text": "docs updated", "done": False}])
    monkeypatch.setattr(
        GF, "_run_decision_turn",
        lambda *a, **k: '{"met": true, "reason": "all done", '
                        '"checklist": [true, false]}')
    verdict, reason, _q, _o = G.evaluate_goal("s1", goal, agent_id="main")
    assert verdict == "unmet"
    assert "清单未全部完成" in reason
    assert "2) docs updated" in reason and "tests green" not in reason

    # met with NO per-item flags still cannot pass an undone list.
    goal2 = _set_goal(tmp_db, "s2", checklist=[{"text": "x", "done": False}])
    monkeypatch.setattr(GF, "_run_decision_turn",
                        lambda *a, **k: '{"met": true, "reason": "done"}')
    verdict2, reason2, _q2, _o2 = G.evaluate_goal("s2", goal2,
                                                  agent_id="main")
    assert verdict2 == "unmet" and "1) x" in reason2

    # All items ticked → met stands.
    goal3 = _set_goal(tmp_db, "s3", checklist=[{"text": "x", "done": False}])
    monkeypatch.setattr(
        GF, "_run_decision_turn",
        lambda *a, **k: '{"met": true, "reason": "done", '
                        '"checklist": [true]}')
    verdict3, _r3, _q3, _o3 = G.evaluate_goal("s3", goal3, agent_id="main")
    assert verdict3 == "met"
    assert goal3["checklist"][0]["done"] is True


def test_continuation_prompt_names_undone_items(tmp_db: SessionDB,
                                                monkeypatch) -> None:
    _set_goal(tmp_db, "s1", checklist=[
        {"text": "a", "done": False}, {"text": "b", "done": False}])
    replies = iter([
        '{"met": false, "reason": "no", "checklist": [true, false]}',
        '{"met": true, "reason": "done", "checklist": [true, true]}',
    ])
    monkeypatch.setattr(GF, "_run_decision_turn",
                        lambda *a, **k: next(replies))
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    assert len(calls) == 1
    text = calls[0].user_text
    assert "未完成项：" in text
    assert "2. b" in text and "1. a" not in text   # only undone items listed
    goal = G.load_goal("s1")
    assert goal["status"] == "achieved"
    assert [it["done"] for it in goal["checklist"]] == [True, True]


def test_emit_goal_update_payload_includes_checklist(tmp_db: SessionDB) -> None:
    events: list[dict] = []
    goal = {"text": "t", "status": "active", "turns_used": 1,
            "max_turns": None, "last_reason": "",
            "checklist": [{"text": "a", "done": True}]}
    _REAL_EMIT_GOAL_UPDATE(lambda ev: events.append(ev), "s1", goal)
    payload = events[0]["data"]
    assert payload["type"] == "goal_update"
    assert payload["goal"]["checklist"] == [{"text": "a", "done": True}]


# ---------------------------------------------------------------------------
# Loop unit tests (fake run_turn)
# ---------------------------------------------------------------------------

def _judge_raw(monkeypatch, fn) -> None:
    """Stub the decision turn's raw-reply seam (``GF._run_decision_turn``)."""
    monkeypatch.setattr(GF, "_run_decision_turn", fn)


def _judge_replies(monkeypatch, replies: list[str]) -> None:
    it = iter(replies)
    _judge_raw(monkeypatch, lambda *a, **k: next(it))


def test_judge_flips_to_achieved(tmp_db: SessionDB, monkeypatch,
                                 captured_goal_events) -> None:
    """Turn 1 judged unmet → one continuation runs → judged met →
    achieved."""
    _set_goal(tmp_db, "s1")
    _judge_replies(monkeypatch, [
        '{"met": false, "reason": "not yet"}',
        '{"met": true, "reason": "done"}',
    ])

    continuations: list[TurnRequest] = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        continuations.append(req)
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


def test_continuation_forces_web_search(tmp_db: SessionDB,
                                        monkeypatch) -> None:
    """goal_continue turns are unattended autonomous work — web_search
    is forced on top of the inherited tool config (per turn only)."""
    _set_goal(tmp_db, "s1")
    _judge_replies(monkeypatch, [
        '{"met": false, "reason": "not yet"}',
        '{"met": true, "reason": "done"}',
    ])
    continuations: list[TurnRequest] = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        continuations.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    # The triggering request had tools_override=None (agent profile) —
    # the continuation lifts that into a full intent + web_search.
    assert continuations[0].tools_override == {
        "enabled": True, "web_search": True}


def test_tools_with_forced_web_search_variants() -> None:
    f = G._tools_with_forced_web_search
    assert f(None) == {"enabled": True, "web_search": True}
    assert f({"enabled": True, "toolset": "research"}) == {
        "enabled": True, "toolset": "research", "web_search": True}
    assert f(["read"]) == ["read", "web_search"]
    assert f(["web_search"]) == ["web_search"]     # already there — no dupe
    assert f([]) == ["web_search"]                 # tools-off still searches


def test_max_turns_caps_the_loop(tmp_db: SessionDB, monkeypatch) -> None:
    _set_goal(tmp_db, "s1", max_turns=2)
    _judge_raw(monkeypatch, lambda *a, **k: '{"met": false, "reason": "no"}')
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "capped"
    assert goal["turns_used"] == 2
    assert len(calls) == 1  # turn 2 ran, turn 3 was never launched


def test_no_cap_by_default(tmp_db: SessionDB, monkeypatch) -> None:
    """max_turns=None (the default) never caps — the loop runs past the
    old 20-turn number and stops only on met."""
    _set_goal(tmp_db, "s1")            # max_turns None
    replies = ['{"met": false, "reason": "no"}'] * 25 + [
        '{"met": true, "reason": "done"}']
    _judge_replies(monkeypatch, replies)
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "achieved"
    assert goal["turns_used"] == 26
    assert len(calls) == 25


def test_clear_mid_loop_stops_continuation(tmp_db: SessionDB,
                                           monkeypatch) -> None:
    _set_goal(tmp_db, "s1")
    _judge_raw(monkeypatch, lambda *a, **k: '{"met": false, "reason": "no"}')
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
    _set_goal(tmp_db, "s1")
    _judge_raw(monkeypatch,
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


def test_zero_tool_continuation_is_idle_error(tmp_db: SessionDB,
                                              monkeypatch) -> None:
    _set_goal(tmp_db, "s1")
    _judge_raw(monkeypatch, lambda *a, **k: '{"met": false, "reason": "no"}')
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
    _set_goal(tmp_db, "s1")
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
    _judge_replies(monkeypatch, [
        "not json at all", '{"met": true, "reason": "done"}'])
    verdict, reason, _question, _opts = G.evaluate_goal(
        "s1", {"text": "the goal"}, agent_id="main")
    assert verdict == "met"
    assert reason == "done"


def test_judge_json_extraction() -> None:
    ok = GF._parse_decision('```json\n{"met": false, "reason": "missing"}\n```')
    assert ok == {"met": False, "reason": "missing",
                  "need_user": False, "question": "", "options": [],
                  "checklist": None}
    with pytest.raises(ValueError):
        GF._parse_decision("no braces here")
    with pytest.raises(ValueError):
        GF._parse_decision('{"met": "yes"}')  # met must be bool


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
        ws, {"text": "/goal tests pass", "session_id": "web2"}))
    assert started.wait(5), "turn thread never launched"

    goal = G.load_goal("web2")
    assert goal["status"] == "active"
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
    """Real process_user_turn: turn 1 is judged unmet, the goal loop
    launches ONE continuation turn (a first-class turn — its own
    user+assistant rows), the second judgement is met, and the goal
    ends achieved."""
    _set_goal(tmp_db, "e2e")
    _judge_replies(monkeypatch, [
        '{"met": false, "reason": "not yet"}',
        '{"met": true, "reason": "done"}',
    ])

    turn_count = {"n": 0}

    def _on_stream_call():
        turn_count["n"] += 1

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
    _set_goal(tmp_db, "s1")
    _judge_raw(monkeypatch,
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
    assert goal["last_question_at"] > 0     # rate-limit clock started
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
    _judge_raw(monkeypatch, lambda *a, **k: next(answers))
    G.continue_goal_turns(_req(source="web"), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "achieved"
    assert "last_question" not in goal
    assert goal["last_question_at"] > 0     # clock persists across resume
    assert len(calls) == 1      # exactly the one resumed continuation


def test_needs_user_rate_limited_degrades_to_continuation(
        tmp_db: SessionDB, monkeypatch) -> None:
    """A needs_user verdict within an hour of the last question does not
    pause — it degrades into a continuation telling the agent to decide
    by itself."""
    _set_goal(tmp_db, "s1", last_question_at=time.time())
    replies = iter([
        ('{"met": false, "reason": "direction unclear", '
         '"need_user": true, "question": "A还是B？"}'),
        '{"met": true, "reason": "done"}',
    ])
    _judge_raw(monkeypatch, lambda *a, **k: next(replies))
    calls = []

    def run_turn(req, *, on_event=None, cancel_event=None):
        calls.append(req)
        return _result(tools=True)

    G.continue_goal_turns(_req(), _result(), run_turn=run_turn)
    goal = G.load_goal("s1")
    assert goal["status"] == "achieved"     # never paused
    assert len(calls) == 1
    assert "提问额度已用" in calls[0].user_text
    assert "A还是B？" in calls[0].user_text
    assert "last_question" not in goal


def test_needs_user_after_interval_pauses_again(tmp_db: SessionDB,
                                                monkeypatch) -> None:
    """An old last_question_at (beyond the 1-hour window) does not block
    a new pause."""
    _set_goal(tmp_db, "s1", last_question_at=time.time() - 7200)
    _judge_raw(monkeypatch,
               lambda *a, **k: ('{"met": false, "reason": "r", '
                                '"need_user": true, "question": "问一下？"}'))
    G.continue_goal_turns(_req(), _result(),
                          run_turn=lambda req, **k: _result())
    goal = G.load_goal("s1")
    assert goal["status"] == "waiting_user"
    assert goal["last_question"] == "问一下？"


def test_needs_user_without_question_continues(tmp_db: SessionDB,
                                               monkeypatch) -> None:
    """need_user=true with an empty question is not actionable — the
    loop treats it as unmet and keeps going."""
    _set_goal(tmp_db, "s1", max_turns=1)
    _judge_raw(monkeypatch,
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
# Goal sessions never enter the turn.stop gate
# ---------------------------------------------------------------------------

def _run_e2e_turn(monkeypatch, session_id: str) -> None:
    fake_stream = _make_text_stream_fn(["done"])
    orig = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None,
                 **extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake_stream, **extra)

    with patch.object(D, "_run_loop_blocking", _wrapped):
        D.process_user_turn(TurnRequest(
            session_id=session_id, user_text="start",
            agent_id="main", source="tui"))


def test_goal_session_skips_stop_gate(tmp_db: SessionDB,
                                      monkeypatch) -> None:
    from openprogram.agent.dispatcher import stop_hook as SH
    gate_calls = []
    monkeypatch.setattr(
        SH, "continue_stop_hook_turns",
        lambda req, result, **k: gate_calls.append(req.session_id) or result)

    # Goal session: the goal loop is the sole stop decider.
    _set_goal(tmp_db, "g1")
    _judge_raw(monkeypatch, lambda *a, **k: '{"met": true, "reason": "done"}')
    _run_e2e_turn(monkeypatch, "g1")
    assert gate_calls == []
    assert G.load_goal("g1")["status"] == "achieved"

    # Session without a goal: the gate runs.
    tmp_db.create_session("plain1", "main")
    _run_e2e_turn(monkeypatch, "plain1")
    assert gate_calls == ["plain1"]


def test_spawned_turns_skip_goal_loop(monkeypatch):
    """A source="agent_spawn" turn (goal decision, task agent) never
    enters the goal loop or the stop gate — otherwise the decision turn
    judges itself and recurses."""
    from openprogram.agent import dispatcher as disp

    called = {"goal": False, "once": 0}

    def fake_once(req, *, on_event=None, cancel_event=None):
        called["once"] += 1
        class _R:
            failed = False
        return _R()

    def fake_goal(*a, **k):
        called["goal"] = True

    monkeypatch.setattr(disp, "_process_turn_once", fake_once)
    import openprogram.programs.workflow.goal as goal_mod
    monkeypatch.setattr(goal_mod, "continue_goal_turns", fake_goal)

    req = disp.TurnRequest(session_id="s1", user_text="x",
                           agent_id="main", source="agent_spawn")
    disp.process_user_turn(req)
    assert called["once"] == 1
    assert called["goal"] is False
