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
