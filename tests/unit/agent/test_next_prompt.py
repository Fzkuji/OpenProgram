"""The composer's suggested next message comes from the last turns and stays a short draft."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation.turn_runtime import next_prompt


@pytest.fixture
def history(monkeypatch):
    rows: list[dict] = []
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
