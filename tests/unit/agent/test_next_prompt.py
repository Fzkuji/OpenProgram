"""The composer's suggested next message comes from the last turns and stays a short draft."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation.turn_runtime import next_prompt


@pytest.fixture
def history(monkeypatch):
    rows: list[dict] = []
    next_prompt._cache.clear()
    import openprogram.runtime.context.session_db as session_db
    monkeypatch.setattr(session_db, "default_db", lambda: SimpleNamespace(get_branch=lambda sid: rows))
    return rows


def test_the_suggestion_reads_recent_turns(history):
    history.extend([{"role": "user", "content": "fix the build"},
                    {"role": "code", "content": "ran tests"},
                    {"role": "assistant", "content": "The build is fixed."}])
    seen = {}

    def llm(system, user):
        seen["user"] = user
        return '"Now run the full test suite"\nextra'

    assert next_prompt.predict_next_prompt("s", llm=llm) == "Now run the full test suite"
    assert "USER: fix the build" in seen["user"] and "ASSISTANT: The build is fixed." in seen["user"]
    assert "ran tests" not in seen["user"]


def test_no_suggestion_while_the_user_spoke_last_or_the_model_declines(history):
    history.append({"role": "user", "content": "hello"})
    assert next_prompt.predict_next_prompt("s", llm=lambda *_: "anything") is None
    history.append({"role": "assistant", "content": "hi"})
    assert next_prompt.predict_next_prompt("s", llm=lambda *_: "NONE") is None
    assert next_prompt.predict_next_prompt("s", llm=lambda *_: (_ for _ in ()).throw(RuntimeError())) is None


def test_long_replies_are_cut():
    assert len(next_prompt.clean_prediction("x" * 500)) == 200
    assert next_prompt.clean_prediction("   ") is None


def test_coming_back_reuses_the_prediction_until_a_new_turn(history):
    history.extend([{"id": "u1", "role": "user", "content": "fix it"},
                    {"id": "a1", "role": "assistant", "content": "Fixed."}])
    calls = []

    def llm(system, user):
        calls.append(user)
        return f"next {len(calls)}"

    assert next_prompt.predict_next_prompt("s", llm=llm) == "next 1"
    assert next_prompt.predict_next_prompt("s", llm=llm) == "next 1"
    history.extend([{"id": "u2", "role": "user", "content": "and docs"},
                    {"id": "a2", "role": "assistant", "content": "Done."}])
    assert next_prompt.predict_next_prompt("s", llm=llm) == "next 2"
    assert len(calls) == 2


def test_an_old_conversation_opened_later_gets_none(history):
    history.extend([{"id": "u1", "role": "user", "content": "fix it"},
                    {"id": "a1", "role": "assistant", "content": "Fixed.", "timestamp": time.time() - 3 * 86400}])
    assert next_prompt.predict_next_prompt("s", llm=lambda *_: "go", max_age=next_prompt.OPENED_MAX_AGE_S) is None
    assert next_prompt.predict_next_prompt("s", llm=lambda *_: "go") == "go"


def test_stray_brackets_are_dropped():
    assert next_prompt.clean_prediction("继续说完】【。") == "继续说完。"
