"""ask_user records a user-role Call in the DAG when a Runtime store
is attached.

DAG modeling:
  role = user            (callee — the human producing the answer)
  input = {"question":}  (what the LLM/code asked)
  output = the answer    (what the user produced)
  called_by = enclosing  (the @agentic_function or LLM that asked)
  metadata.status        "awaiting" → "answered" / "unanswered"

When no Runtime / store is attached, ask_user still works via the
global handler / TTY — DAG persistence is purely additive.
"""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.agentic_programming.function import (
    _current_runtime, _current_function_frame,
)
from openprogram.context.storage import GraphStore, init_db
from openprogram.programs.functions.buildin.ask_user import (
    ask_user, set_ask_user,
)


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    db = tmp_path / "x.sqlite"
    init_db(db)
    s = GraphStore(db, "s1")
    s.create_session_row()
    return s


@pytest.fixture
def rt() -> Runtime:
    return Runtime(call=lambda *a, **kw: "", model="dummy")


@contextmanager
def _install_handler(handler):
    """Set a global ask_user handler for the scope of the test, restore
    whatever was there before on exit."""
    from openprogram.programs.functions.buildin import ask_user as _au
    with _au._ask_user_lock:
        prev = _au._ask_user_handler_global
    set_ask_user(handler)
    try:
        yield
    finally:
        set_ask_user(prev)


@contextmanager
def _install_runtime(rt):
    token = _current_runtime.set(rt)
    try:
        yield
    finally:
        _current_runtime.reset(token)


@contextmanager
def _install_frame(pending_id):
    token = _current_function_frame.set(pending_id)
    try:
        yield
    finally:
        _current_function_frame.reset(token)


# ── No store: still works, no DAG write ─────────────────────────────


def test_ask_user_without_store_works_via_handler(rt):
    """No store attached → handler still answers, nothing written."""
    with _install_runtime(rt), _install_handler(lambda q: "no-store answer"):
        ans = ask_user("how are you?")
    assert ans == "no-store answer"
    assert rt.head_id is None      # no DAG write


def test_ask_user_no_runtime_at_all(rt):
    """Truly standalone: no runtime in ContextVar, just global handler."""
    with _install_handler(lambda q: "standalone answer"):
        ans = ask_user("hi")
    assert ans == "standalone answer"


# ── With store: full lifecycle ──────────────────────────────────────


def test_ask_user_records_user_call_with_question_and_answer(rt, store):
    """Attached store + handler: a user-role Call lands with
    input.question = the question, output = the answer."""
    rt.attach_store(store)
    with _install_runtime(rt), _install_handler(lambda q: f"answered: {q}"):
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


def test_ask_user_called_by_set_to_frame_when_inside_function(rt, store):
    """Inside an @agentic_function frame, the placeholder Call's
    called_by points at the enclosing function's pending id."""
    rt.attach_store(store)
    with _install_runtime(rt), _install_handler(lambda q: "ok"), \
            _install_frame("plan_pending_id"):
        ask_user("clarify?")
    g = store.load()
    n = next(x for x in g if x.is_user())
    assert n.called_by == "plan_pending_id"


def test_ask_user_called_by_empty_when_top_level(rt, store):
    """Outside any @agentic_function frame → called_by is empty."""
    rt.attach_store(store)
    with _install_runtime(rt), _install_handler(lambda q: "yes"):
        ask_user("global question")
    g = store.load()
    n = next(x for x in g if x.is_user())
    assert n.called_by == ""


# ── Status transitions ────────────────────────────────────────────


def test_ask_user_status_awaiting_then_answered(rt, store):
    """During the handler call, the node has status='awaiting'; after
    the handler returns, status flips to 'answered'."""
    rt.attach_store(store)
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

    with _install_runtime(rt), _install_handler(_slow_handler):
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


def test_ask_user_unanswered_when_handler_returns_none(rt, store):
    """status='unanswered' covers the no-answer outcome (handler
    returned None / empty)."""
    rt.attach_store(store)
    with _install_runtime(rt), _install_handler(lambda q: None):
        ans = ask_user("noanswer?")
    assert ans is None
    g = store.load()
    n = next(x for x in g if x.is_user())
    assert n.metadata.get("status") == "unanswered"


# ── Distinguishes from spontaneous user message ────────────────────


def test_ask_user_call_has_input_not_none_unlike_spontaneous_msg(rt, store):
    """A spontaneous user message has input=None; an ask_user response
    has input={"question": ...}. That difference is the DAG-level
    distinction between the two kinds of user Call."""
    rt.attach_store(store)

    # Spontaneous: dispatcher writes this with no input
    from openprogram.context.nodes import Call, ROLE_USER
    rt.append_node(Call(role=ROLE_USER, output="hi", input=None))

    # Provoked by ask_user
    with _install_runtime(rt), _install_handler(lambda q: "yes"):
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
