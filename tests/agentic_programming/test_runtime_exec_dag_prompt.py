"""Runtime._call_via_providers (AgentSession path) builds its prompt
from the DAG when a store is attached.

Verified by spying on ``_render_dag_messages_for_exec``: when the
runtime has a store, this returns a non-None list pulled from the
graph; when no store is attached, it returns None (legacy path
kicks in).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM
from openprogram.context.storage import GraphStore, init_db


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


# ── No store: DAG path returns None ───────────────────────────────


def test_no_store_returns_none(rt):
    """Without an attached store the DAG helper must return None so
    the legacy render_messages path stays active."""
    msgs = rt._render_dag_messages_for_exec(
        content=[{"type": "text", "text": "hello"}],
    )
    assert msgs is None


# ── With store: DAG history shows up + current turn appended ──────


def test_with_store_builds_history_plus_current(rt, store):
    """Attach a store containing prior user + assistant messages.
    Calling the helper should return those + a fresh UserMessage
    built from ``content``."""
    rt.attach_store(store)
    rt.append_node(Call(role=ROLE_USER, output="hi"))
    rt.append_node(Call(role=ROLE_LLM, output="hello back"))

    msgs = rt._render_dag_messages_for_exec(
        content=[{"type": "text", "text": "what next?"}],
    )
    assert msgs is not None
    roles = [m.role for m in msgs]
    # prior user + prior llm + fresh user (current turn)
    assert roles == ["user", "assistant", "user"]
    last_text = msgs[-1].content[0].text
    assert last_text == "what next?"


def test_empty_store_yields_only_current_turn(rt, store):
    """Attached but empty store: history is empty; current-turn user
    message still gets synthesized."""
    rt.attach_store(store)
    msgs = rt._render_dag_messages_for_exec(
        content=[{"type": "text", "text": "first ping"}],
    )
    assert msgs is not None
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content[0].text == "first ping"


def test_multiple_text_blocks_joined_with_newline(rt, store):
    rt.attach_store(store)
    msgs = rt._render_dag_messages_for_exec(content=[
        {"type": "text", "text": "line 1"},
        {"type": "text", "text": "line 2"},
    ])
    assert msgs[-1].content[0].text == "line 1\nline 2"


# ── Inside an @agentic_function frame ───────────────────────────────


def test_dag_prompt_inside_io_function_frame(rt, store):
    """Simulate the state ``_call_via_providers`` would see when
    ``runtime.exec`` is invoked from inside an @agentic_function
    body with expose='io':

      n0  user        "find weather"
      n1  llm         "let me check"          (response to user)
      n2  code/plan   running, expose=io      (placeholder, function entered)

    Then the body calls runtime.exec. _current_function_frame is set
    to n2.id. We expect:
      - history includes n0, n1, n2 (as call signature)
      - n2 is NOT excluded just because frame_entry == n2
      - last message is the current turn synthesized from content
    """
    from openprogram.agentic_programming.function import (
        _current_function_frame,
    )
    from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM, ROLE_CODE

    rt.attach_store(store)
    n0 = rt.append_node(Call(role=ROLE_USER, output="find weather")) or store.load()
    n0 = store.load()  # get the freshly stored Call back
    user_node = next(n for n in n0 if n.is_user())

    rt.append_node(Call(role=ROLE_LLM, output="let me check"))
    plan_node = Call(
        role=ROLE_CODE,
        name="plan",
        input={"task": "weather"},
        output=None,
        metadata={"expose": "io", "status": "running"},
    )
    rt.append_node(plan_node)

    token = _current_function_frame.set(plan_node.id)
    try:
        msgs = rt._render_dag_messages_for_exec(
            content=[{"type": "text", "text": "step 1"}],
        )
    finally:
        _current_function_frame.reset(token)

    assert msgs is not None
    roles = [m.role for m in msgs]
    # user + assistant from chat + (user from code call sig, no assistant
    # because output=None) + current turn
    assert "user" in roles
    assert "assistant" in roles
    # Current turn is the last message
    assert msgs[-1].content[0].text == "step 1"


def test_render_range_depth_zero_hides_history(rt, store):
    """When inside a frame with render_range={'depth':0}, prior chat
    history is walled off — only in-frame nodes + current turn appear."""
    from openprogram.agentic_programming.function import (
        _current_function_frame,
    )
    from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM, ROLE_CODE

    rt.attach_store(store)
    rt.append_node(Call(role=ROLE_USER, output="prior chat user"))
    rt.append_node(Call(role=ROLE_LLM, output="prior chat reply"))
    isolated = Call(
        role=ROLE_CODE, name="isolated", input={}, output=None,
        metadata={"expose": "io", "status": "running",
                  "render_range": {"depth": 0, "siblings": 99}},
    )
    rt.append_node(isolated)

    token = _current_function_frame.set(isolated.id)
    try:
        msgs = rt._render_dag_messages_for_exec(
            content=[{"type": "text", "text": "isolated turn"}],
        )
    finally:
        _current_function_frame.reset(token)

    assert msgs is not None
    texts = [
        c.text for m in msgs for c in m.content if hasattr(c, "text")
    ]
    # Prior chat is gone
    assert "prior chat user" not in texts
    assert "prior chat reply" not in texts
    # Current turn present
    assert "isolated turn" in texts
