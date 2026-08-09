"""render_session_transcript + the read_conversation tool registration."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openprogram.store import SessionStore
from openprogram.store.session.transcript import render_session_transcript


@pytest.fixture
def db(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions-git")


def _user(db, sess, mid, text, parent=None):
    db.append_message(sess, {
        "id": mid, "role": "user", "content": text,
        "predecessor": parent, "timestamp": time.time(),
    })


def _assistant(db, sess, mid, text, parent):
    db.append_message(sess, {
        "id": mid, "role": "assistant", "content": text,
        "predecessor": parent, "timestamp": time.time(),
    })


def _call(db, sess, mid, caller, name, args, result, *, is_error=False):
    """A tool node — hangs off its caller turn, not the conv chain."""
    db.append_message(sess, {
        "id": mid, "role": "tool", "content": result,
        "function": name, "caller": caller, "predecessor": None,
        "is_error": is_error, "timestamp": time.time(),
        "extra": json.dumps({"tool_use": {"name": name, "arguments": args}}),
    })


@pytest.fixture
def session(db) -> str:
    sid = "s1"
    db.create_session(sid, agent_id="a")
    _user(db, sid, "u1", "count the python files")
    _assistant(db, sid, "a1", "Running a shell command.", "u1")
    _call(db, sid, "t1", "a1", "bash", {"command": "ls *.py | wc -l"}, "42")
    _user(db, sid, "u2", "now delete them", "a1")
    _assistant(db, sid, "a2", "That failed.", "u2")
    _call(db, sid, "t2", "a2", "bash", {"command": "rm *.py"},
          "Permission denied", is_error=True)
    return sid


# Turn rendering


def test_renders_every_turn_in_order(db, session):
    out = render_session_transcript(session, store=db)
    assert "count the python files" in out
    assert "now delete them" in out
    assert out.index("count the python files") < out.index("now delete them")
    assert "4 turns" in out  # conv chain only: u1 a1 u2 a2


def test_headers_label_roles(db, session):
    out = render_session_transcript(session, store=db)
    assert "--- [1] user ---" in out
    assert "--- [2] assistant ---" in out


# Function calls


def test_includes_calls_with_name_args_and_result(db, session):
    out = render_session_transcript(session, store=db)
    assert "[call] bash -> ok" in out
    assert "ls *.py | wc -l" in out
    assert "42" in out


def test_marks_failed_calls(db, session):
    out = render_session_transcript(session, store=db)
    assert "[call] bash -> FAILED" in out
    assert "Permission denied" in out


def test_calls_attach_under_their_own_turn(db, session):
    out = render_session_transcript(session, store=db)
    # The successful call belongs to turn 2, the failed one to turn 4.
    assert out.index("ls *.py | wc -l") < out.index("now delete them")
    assert out.index("rm *.py") > out.index("now delete them")


def test_include_function_calls_false_drops_them(db, session):
    out = render_session_transcript(session, include_function_calls=False, store=db)
    assert "count the python files" in out
    assert "[call]" not in out


# Branch selection


def test_head_id_selects_a_different_branch(db):
    sid = "s2"
    db.create_session(sid, agent_id="a")
    _user(db, sid, "u1", "shared start")
    _assistant(db, sid, "a1", "trunk reply", "u1")
    _assistant(db, sid, "a2", "fork reply", "u1")  # sibling branch off u1
    trunk = render_session_transcript(sid, head_id="a1", store=db)
    fork = render_session_transcript(sid, head_id="a2", store=db)
    assert "trunk reply" in trunk and "fork reply" not in trunk
    assert "fork reply" in fork and "trunk reply" not in fork


# Turn range


def test_range_keeps_global_turn_numbers(db, session):
    out = render_session_transcript(session, start_turn=2, end_turn=3, store=db)
    assert "--- [2] assistant ---" in out
    assert "--- [3] user ---" in out
    assert "--- [1]" not in out and "--- [4]" not in out
    assert "turns 2-3 of 4" in out


def test_negative_range_reads_the_tail(db, session):
    out = render_session_transcript(session, start_turn=-2, store=db)
    assert "--- [3] user ---" in out
    assert "--- [4] assistant ---" in out
    assert "--- [1]" not in out
    assert "turns 3-4 of 4" in out


def test_empty_range_returns_notice(db, session):
    out = render_session_transcript(session, start_turn=9, store=db)
    assert out == "[transcript] range selects no turns (session has 4 turns)"


def test_default_range_reads_everything(db, session):
    assert render_session_transcript(session, store=db) == \
        render_session_transcript(session, start_turn=0, end_turn=0, store=db)
    assert "4 turns" in render_session_transcript(session, store=db)


# Truncation and empties


def test_truncates_to_budget_and_says_so(db, session):
    out = render_session_transcript(session, max_chars=200, store=db)
    assert len(out) < 800
    assert "transcript truncated" in out
    assert "start_turn=" in out
    assert "now delete them" not in out


def test_long_content_is_clipped_per_field(db):
    sid = "s3"
    db.create_session(sid, agent_id="a")
    _user(db, sid, "u1", "x" * 50_000)
    out = render_session_transcript(sid, store=db)
    assert "chars truncated" in out
    assert len(out) < 10_000


def test_missing_session_returns_notice_not_error(db):
    out = render_session_transcript("does-not-exist", store=db)
    assert out.startswith("[transcript]")


# Tool registration


def test_tool_is_registered_with_a_valid_schema():
    import openprogram.functions.tools  # noqa: F401 — fires registration
    from openprogram.functions import _runtime

    tool = _runtime.get("read_conversation")
    assert tool is not None
    schema = tool.parameters
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {
        "session_id", "head_id", "start_turn", "end_turn",
        "include_function_calls", "max_chars",
    }
    # Every parameter optional: the tool defaults to the current session.
    assert not schema.get("required")
    assert tool.description
