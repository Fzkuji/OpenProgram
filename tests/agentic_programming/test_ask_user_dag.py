"""ask_user records a user-role Call in the DAG when ``_store`` is
installed.

DAG modeling:
  role = user            (callee — the human producing the answer)
  input = {"question":}  (what the LLM/code asked)
  output = the answer    (what the user produced)
  called_by = enclosing  (the @agentic_function or LLM that asked)
  metadata.status        "awaiting" → "answered" / "unanswered"

When no store is installed, ask_user still works via the global
handler / TTY — DAG persistence is purely additive.
"""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager

import pytest

from openprogram.agentic_programming.function import _call_id
from openprogram.store import GraphStoreShim, SessionStore, _store as _store_var
from openprogram.functions.agentics.ask_user import (
    ask_user, set_ask_user,
)


@pytest.fixture
def store(tmp_path: Path):
    """GraphStore installed into ``_store`` for the test's duration."""
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", agent_id="main")
    s = GraphStoreShim(store, "s1")
    token = _store_var.set(s)
    try:
        yield s
    finally:
        _store_var.reset(token)


@contextmanager
def _install_handler(handler):
    """Set a global ask_user handler for the scope of the test, restore
    whatever was there before on exit."""
    from openprogram.functions.agentics import ask_user as _au
    with _au._ask_user_lock:
        prev = _au._ask_user_handler_global
    set_ask_user(handler)
    try:
        yield
    finally:
        set_ask_user(prev)


@contextmanager
def _install_frame(pending_id):
    token = _call_id.set(pending_id)
    try:
        yield
    finally:
        _call_id.reset(token)


# ── No store: still works, no DAG write ─────────────────────────────


def test_ask_user_without_store_works_via_handler():
    """No store installed → handler still answers, nothing written."""
    with _install_handler(lambda q: "no-store answer"):
        ans = ask_user("how are you?")
    assert ans == "no-store answer"


def test_ask_user_no_store_at_all():
    """Truly standalone: no store in ContextVar, just global handler."""
    with _install_handler(lambda q: "standalone answer"):
        ans = ask_user("hi")
    assert ans == "standalone answer"


# ── With store: full lifecycle ──────────────────────────────────────


def test_ask_user_records_user_call_with_question_and_answer(store):
    """Installed store + handler: a user-role Call lands with
    input.question = the question, output = the answer."""
    with _install_handler(lambda q: f"answered: {q}"):
        ans = ask_user("weather?")
    assert ans == "answered: weather?"

    g = store.load()
    user_nodes = [n for n in g if n.is_user()]
    assert len(user_nodes) == 1
    n = user_nodes[0]
    assert isinstance(n.input, dict)
    assert n.input["question"] == "weather?"
    assert n.output == "answered: weather?"
    assert n.metadata.get("status") == "answered"


def test_ask_user_called_by_set_to_frame_when_inside_function(store):
    """Inside an @agentic_function frame, the placeholder Call's
    called_by points at the enclosing function's pending id."""
    with _install_handler(lambda q: "ok"), _install_frame("plan_pending_id"):
        ask_user("clarify?")
    g = store.load()
    n = next(x for x in g if x.is_user())
    assert n.called_by == "plan_pending_id"


def test_ask_user_called_by_empty_when_top_level(store):
    """Outside any @agentic_function frame → called_by is empty."""
    with _install_handler(lambda q: "yes"):
        ask_user("global question")
    g = store.load()
    n = next(x for x in g if x.is_user())
    assert n.called_by == ""


# ── Status transitions ────────────────────────────────────────────


def test_ask_user_status_awaiting_then_answered(store):
    """During the handler call, the node has status='awaiting'; after
    the handler returns, status flips to 'answered'."""
    snapshot_during: list = []

    def _slow_handler(q):
        # Inside handler: snapshot the node mid-flight.
        g = store.load()
        for n in g:
            if n.is_user():
                snapshot_during.append(
                    (n.input, n.output, n.metadata.get("status"))
                )
        return "final answer"

    with _install_handler(_slow_handler):
        ask_user("anything?")

    # Mid-handler: input had the question, output None, status awaiting
    assert len(snapshot_during) == 1
    mid_input, mid_output, mid_status = snapshot_during[0]
    assert mid_input == {"question": "anything?"}
    assert mid_output is None
    assert mid_status == "awaiting"

    # After: output filled, status answered
    g = store.load()
    n = next(x for x in g if x.is_user())
    assert n.output == "final answer"
    assert n.metadata.get("status") == "answered"


def test_ask_user_unanswered_when_handler_returns_none(store):
    """status='unanswered' covers the no-answer outcome (handler
    returned None / empty)."""
    with _install_handler(lambda q: None):
        ans = ask_user("noanswer?")
    assert ans is None
    g = store.load()
    n = next(x for x in g if x.is_user())
    assert n.metadata.get("status") == "unanswered"


# ── Distinguishes from spontaneous user message ────────────────────


def test_ask_user_call_has_input_not_none_unlike_spontaneous_msg(store):
    """A spontaneous user message has input=None; an ask_user response
    has input={"question": ...}. That difference is the DAG-level
    distinction between the two kinds of user Call."""
    from openprogram.context.nodes import Call, ROLE_USER

    # Spontaneous: dispatcher writes this with no input
    store.append(Call(role=ROLE_USER, output="hi", input=None))

    # Provoked by ask_user
    with _install_handler(lambda q: "yes"):
        ask_user("really?")

    g = store.load()
    users = [n for n in g if n.is_user()]
    assert len(users) == 2

    # The spontaneous one has input=None
    spontaneous = [n for n in users if n.input is None]
    # The provoked one has input.question
    provoked = [n for n in users
                if isinstance(n.input, dict) and "question" in n.input]
    assert len(spontaneous) == 1
    assert len(provoked) == 1
    assert spontaneous[0].output == "hi"
    assert provoked[0].input == {"question": "really?"}
