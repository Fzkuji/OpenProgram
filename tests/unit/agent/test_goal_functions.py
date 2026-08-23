"""Unit tests for the goal agentic function
(``openprogram/programs/workflow/goal/``): the single decision entry
``goal`` — prompt assembly, JSON parse path, failure path. The loop
semantics around it live in test_goal_loop.py."""
from __future__ import annotations

import pytest

from openprogram.agent.sub_agent_run import AgentTurnResult

import openprogram.programs.workflow.goal.judge as GJ
import openprogram.programs.workflow.goal.refinement as GR


@pytest.fixture
def stub_view(monkeypatch):
    monkeypatch.setattr(GJ, "render_session_view", lambda sid, **k: "VIEW")


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------

def test_parse_decision_fenced_json() -> None:
    ok = GJ._parse_decision(
        '```json\n{"met": false, "reason": "missing"}\n```')
    assert ok == {"met": False, "reason": "missing",
                  "need_user": False, "question": "", "options": [],
                  "checklist": None}


def test_parse_decision_invalid_raises() -> None:
    with pytest.raises(ValueError):
        GJ._parse_decision("no braces here")
    with pytest.raises(ValueError):
        GJ._parse_decision('{"met": "yes"}')  # met must be bool


def test_parse_decision_checklist_cleaning() -> None:
    # Equal-length pure-bool list passes through.
    ok = GJ._parse_decision(
        '{"met": false, "reason": "r", "checklist": [true, false, true]}',
        checklist_len=3)
    assert ok["checklist"] == [True, False, True]
    # Wrong length → None (this round carries no per-item info).
    short = GJ._parse_decision(
        '{"met": false, "reason": "r", "checklist": [true]}',
        checklist_len=3)
    assert short["checklist"] is None
    # Missing → None.
    missing = GJ._parse_decision('{"met": false, "reason": "r"}',
                                 checklist_len=3)
    assert missing["checklist"] is None
    # Non-bool content → None.
    dirty = GJ._parse_decision(
        '{"met": false, "reason": "r", "checklist": [true, "yes", 1]}',
        checklist_len=3)
    assert dirty["checklist"] is None
    # No checklist expected → always None, even when the judge invents one.
    invented = GJ._parse_decision(
        '{"met": false, "reason": "r", "checklist": [true]}')
    assert invented["checklist"] is None


# ---------------------------------------------------------------------------
# The decision entry
# ---------------------------------------------------------------------------

def test_goal_decision_parses_and_forwards(monkeypatch, stub_view) -> None:
    calls = []

    def _fake_turn(session_id, prompt, *, agent_id, spawn_caller):
        calls.append((session_id, prompt, agent_id, spawn_caller))
        return ('prose before {"met": true, "reason": "done", '
                '"need_user": false, "question": ""} after')

    monkeypatch.setattr(GJ, "_run_decision_turn", _fake_turn)
    out = GJ.judge_goal(goal="MY-GOAL", session_id="s1",
                  spawn_caller="a1", agent_id="main")
    assert out == {"met": True, "reason": "done",
                   "need_user": False, "question": "", "options": [],
                   "checklist": None}
    sid, prompt, agent_id, spawn_caller = calls[0]
    assert (sid, agent_id, spawn_caller) == ("s1", "main", "a1")
    assert "completion judge" in prompt              # docstring is the prompt
    assert "<goal>\nMY-GOAL\n</goal>" in prompt
    assert "<session_context>\nVIEW\n</session_context>" in prompt


def test_goal_decision_optional_fields_default(monkeypatch, stub_view) -> None:
    # Replies without need_user/question stay valid.
    monkeypatch.setattr(GJ, "_run_decision_turn",
                        lambda *a, **k: '{"met": false, "reason": "not yet"}')
    out = GJ.judge_goal(goal="g", session_id="s1")
    assert out == {"met": False, "reason": "not yet",
                   "need_user": False, "question": "", "options": [],
                   "checklist": None}


def test_goal_decision_checklist_in_prompt_and_reply(monkeypatch,
                                                    stub_view) -> None:
    prompts = []

    def _fake_turn(session_id, prompt, *, agent_id, spawn_caller):
        prompts.append(prompt)
        return '{"met": false, "reason": "r", "checklist": [true, false]}'

    monkeypatch.setattr(GJ, "_run_decision_turn", _fake_turn)
    out = GJ.judge_goal(goal="g", session_id="s1", checklist=["item A", "item B"])
    assert "<checklist>\n1. item A\n2. item B\n</checklist>" in prompts[0]
    assert out["checklist"] == [True, False]
    # No checklist input → no rendered block (the docstring's own
    # mention of <checklist> stays, the payload block does not).
    GJ.judge_goal(goal="g", session_id="s1")
    assert "<checklist>\n1." not in prompts[1]


def test_goal_decision_invalid_reply_raises(monkeypatch, stub_view) -> None:
    monkeypatch.setattr(GJ, "_run_decision_turn",
                        lambda *a, **k: "no json here")
    with pytest.raises(ValueError):
        GJ.judge_goal(goal="g", session_id="s1")


def test_goal_decision_turn_failure_propagates(monkeypatch, stub_view) -> None:
    monkeypatch.setattr(
        GJ, "_run_decision_turn",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spawn down")))
    with pytest.raises(RuntimeError):
        GJ.judge_goal(goal="g", session_id="s1")


def test_run_decision_turn_passes_judge_model(monkeypatch) -> None:
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return AgentTurnResult(final_text="ok")

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn", fake_run)
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"goal": {"judge_model": "cheap/model"}},
    )
    assert GJ._run_decision_turn(
        "s1", "p", agent_id="main", spawn_caller="a1") == "ok"
    assert captured["model_override"] == "cheap/model"

    captured.clear()
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"goal": {"judge_model": ""}},
    )
    GJ._run_decision_turn("s1", "p", agent_id="main", spawn_caller="a1")
    assert captured["model_override"] is None


# ---------------------------------------------------------------------------
# Spec refinement — the internal `refine` entry
# ---------------------------------------------------------------------------

def test_parse_refinement_valid_and_fenced() -> None:
    assert GR._parse_refinement('{"spec": "do X then Y"}') == ("do X then Y", [])
    assert GR._parse_refinement('```json\n{"spec": " S "}\n```') == ("S", [])


def test_parse_refinement_with_checklist() -> None:
    spec, items = GR._parse_refinement(
        '{"spec": "S", "checklist": [" a ", "", 3, "b"]}')
    assert spec == "S"
    assert items == ["a", "b"]                # cleaned, non-strings dropped
    # More than 20 items are truncated.
    raw = ('{"spec": "S", "checklist": '
           + str([f"item {i}" for i in range(30)]).replace("'", '"') + "}")
    _, capped = GR._parse_refinement(raw)
    assert len(capped) == 20


def test_parse_refinement_prose_fallback_empty_checklist() -> None:
    prose = "A substantial plain-prose specification. " * 10
    spec, items = GR._parse_refinement(prose)
    assert spec == prose.strip()
    assert items == []                        # fail-open: no checklist


def test_parse_refinement_invalid_raises() -> None:
    for raw in ("no json", '{"spec": ""}', '{"spec": 3}', '{"other": "x"}'):
        with pytest.raises(ValueError):
            GR._parse_refinement(raw)


def test_refine_parses_and_forwards(monkeypatch) -> None:
    calls = []

    def _fake_turn(session_id, prompt, *, agent_id, spawn_caller):
        calls.append((session_id, prompt, agent_id, spawn_caller))
        return ('thinking… {"spec": "criteria: tests pass", '
                '"checklist": ["tests pass"]} ')

    monkeypatch.setattr(GR, "_run_refine_turn", _fake_turn)
    out = GR.refine_goal_spec_candidate("tests pass", session_id="s1", agent_id="main")
    assert out == ("criteria: tests pass", ["tests pass"])
    sid, prompt, agent_id, spawn_caller = calls[0]
    assert (sid, agent_id, spawn_caller) == ("s1", "main", None)
    assert "SPECIFICATION" in prompt          # docstring is the prompt
    assert "<goal>\ntests pass\n</goal>" in prompt


def test_refine_turn_failure_propagates(monkeypatch) -> None:
    monkeypatch.setattr(
        GR, "_run_refine_turn",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spawn down")))
    with pytest.raises(RuntimeError):
        GR.refine_goal_spec_candidate("g", session_id="s1")


# ---------------------------------------------------------------------------
# Session view rendering — summary + tail shape
# ---------------------------------------------------------------------------

def test_render_session_view_keeps_summary_and_tail(monkeypatch) -> None:
    rows = ([{"id": "sum", "role": "summary", "content": "SUMMARY",
              "covers_ids": ["a", "b"]}]
            + [{"id": f"m{i}", "role": "user", "content": f"turn {i}"}
               for i in range(20)])
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: None)
    monkeypatch.setattr(
        "openprogram.context.persistence.rendered_history",
        lambda db, sid, head_id=None: rows)
    view = GJ.render_session_view("s1", max_messages=3)
    assert view.startswith("[summary] SUMMARY")   # summary survives the cap
    assert "turn 19" in view                      # tail keeps the newest
    assert "turn 5" not in view                   # older kept turns capped


def test_render_session_view_plain_branch(monkeypatch) -> None:
    rows = [{"id": "m1", "role": "user", "content": "hello"},
            {"id": "m2", "role": "assistant", "content": "hi"}]
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: None)
    monkeypatch.setattr(
        "openprogram.context.persistence.rendered_history",
        lambda db, sid, head_id=None: rows)
    view = GJ.render_session_view("s1")
    assert view == "[user] hello\n[assistant] hi"


# ---------------------------------------------------------------------------
# Attended / unattended mode reaches the prompt
# ---------------------------------------------------------------------------

def test_goal_decision_mode_in_prompt(monkeypatch, stub_view) -> None:
    prompts = []

    def _fake_turn(session_id, prompt, *, agent_id, spawn_caller):
        prompts.append(prompt)
        return '{"met": false, "reason": "r"}'

    monkeypatch.setattr(GJ, "_run_decision_turn", _fake_turn)
    GJ.judge_goal(goal="g", session_id="s1")                    # default attended
    GJ.judge_goal(goal="g", session_id="s1", attended=False)
    assert "<mode>\nattended\n</mode>" in prompts[0]
    assert "<mode>\nunattended\n</mode>" in prompts[1]
    # Both policies are spelled out in the docstring prompt.
    assert "unattended — nobody is watching" in prompts[0]
