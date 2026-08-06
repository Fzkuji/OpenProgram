"""Unit tests for the goal agentic functions
(``openprogram/functions/agentics/goal/``): goal_judge's JSON parse
path and goal_verify's fail-open behaviour. The loop semantics around
them live in test_goal_loop.py."""
from __future__ import annotations

import pytest

import openprogram.functions.agentics.goal as GF


# ---------------------------------------------------------------------------
# goal_judge — parse path
# ---------------------------------------------------------------------------

def test_goal_judge_parses_reply(monkeypatch) -> None:
    monkeypatch.setattr(
        GF, "_judge_reply",
        lambda *a, **k: ('prose before {"met": true, "reason": "ok", '
                         '"need_user": false, "question": ""} after'))
    out = GF.goal_judge(goal="tests pass", transcript_tail="[tool bash] ok")
    assert out == {"met": True, "reason": "ok",
                   "need_user": False, "question": ""}


def test_goal_judge_defaults_optional_fields(monkeypatch) -> None:
    # Older judge outputs without need_user/question stay valid.
    monkeypatch.setattr(
        GF, "_judge_reply",
        lambda *a, **k: '{"met": false, "reason": "missing"}')
    out = GF.goal_judge(goal="g", transcript_tail="t")
    assert out == {"met": False, "reason": "missing",
                   "need_user": False, "question": ""}


def test_goal_judge_invalid_reply_raises(monkeypatch) -> None:
    monkeypatch.setattr(GF, "_judge_reply", lambda *a, **k: "no json here")
    with pytest.raises(ValueError):
        GF.goal_judge(goal="g", transcript_tail="t")


def test_goal_judge_non_bool_met_raises(monkeypatch) -> None:
    monkeypatch.setattr(GF, "_judge_reply", lambda *a, **k: '{"met": "yes"}')
    with pytest.raises(ValueError):
        GF.goal_judge(goal="g", transcript_tail="t")


def test_goal_judge_prompt_carries_docstring_and_payload(monkeypatch) -> None:
    seen = {}

    def _fake_exec(content, toolset=None, response_format=None, **kwargs):
        seen["text"] = content[0]["text"]
        seen["toolset"] = toolset
        seen["response_format"] = response_format
        return '{"met": true, "reason": "done", "need_user": false, "question": ""}'

    class _FakeRuntime:
        exec = staticmethod(_fake_exec)

    out = GF.goal_judge(goal="MY-GOAL", transcript_tail="MY-TAIL",
                        runtime=_FakeRuntime())
    assert out["met"] is True
    assert "strict completion judge" in seen["text"]      # docstring prompt
    assert "<goal>\nMY-GOAL\n</goal>" in seen["text"]
    assert "<transcript_tail>\nMY-TAIL\n</transcript_tail>" in seen["text"]
    assert seen["toolset"] == "none"                      # no-tools judge
    assert seen["response_format"] == GF._JUDGE_RESPONSE_SCHEMA


# ---------------------------------------------------------------------------
# goal_verify — fail-open + parse
# ---------------------------------------------------------------------------

def test_goal_verify_confirmed(monkeypatch) -> None:
    prompts = []

    def _fake_turn(session_id, prompt, *, agent_id, spawn_caller):
        prompts.append((session_id, prompt, agent_id, spawn_caller))
        return '{"confirmed": true, "evidence": "全部测试通过", "gap": ""}'

    monkeypatch.setattr(GF, "_run_verifier_turn", _fake_turn)
    out = GF.goal_verify(goal="tests pass", claim="目标已达成",
                         session_id="s1", spawn_caller="a1", agent_id="main")
    assert out == {"confirmed": True, "evidence": "全部测试通过", "gap": ""}
    sid, prompt, agent_id, spawn_caller = prompts[0]
    assert (sid, agent_id, spawn_caller) == ("s1", "main", "a1")
    assert "不要相信" in prompt                            # docstring prompt
    assert "<claim>\n目标已达成\n</claim>" in prompt


def test_goal_verify_refuted(monkeypatch) -> None:
    monkeypatch.setattr(
        GF, "_run_verifier_turn",
        lambda *a, **k: '{"confirmed": false, "evidence": "3 fail", '
                        '"gap": "测试没过"}')
    out = GF.goal_verify(goal="g", claim="c", session_id="s1")
    assert out == {"confirmed": False, "evidence": "3 fail", "gap": "测试没过"}


def test_goal_verify_turn_failure_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(
        GF, "_run_verifier_turn",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spawn down")))
    out = GF.goal_verify(goal="g", claim="c", session_id="s1")
    assert out == {"confirmed": True, "evidence": "", "gap": ""}


def test_goal_verify_unparseable_reply_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(GF, "_run_verifier_turn",
                        lambda *a, **k: "I looked around, seems fine")
    out = GF.goal_verify(goal="g", claim="c", session_id="s1")
    assert out == {"confirmed": True, "evidence": "", "gap": ""}


def test_goal_verify_non_bool_confirmed_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(GF, "_run_verifier_turn",
                        lambda *a, **k: '{"confirmed": "yes"}')
    out = GF.goal_verify(goal="g", claim="c", session_id="s1")
    assert out == {"confirmed": True, "evidence": "", "gap": ""}
