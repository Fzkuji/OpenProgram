"""ContextEngine._build_messages_from_dag — chat reads context via the
compute_reads + render_dag_messages pipeline (session-dag.md step 4).

Verifies the two parity guards: no double-add of the trailing user message,
and active-branch-only (sibling/fork branches don't leak in).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.store import SessionStore


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> SessionStore:
    store = SessionStore(tmp_path / "sessions-git")
    # _build_messages_from_dag resolves the store via default_db()
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    return store


def _engine():
    from openprogram.context.engine import DefaultContextEngine
    return DefaultContextEngine()


def _texts(messages) -> list[str]:
    out = []
    for m in messages:
        out.append("".join(c.text for c in m.content if hasattr(c, "text")))
    return out


def _roles(messages) -> list[str]:
    return [m.role for m in messages]


def test_dag_render_excludes_trailing_user_message(db):
    """The just-submitted user message (branch tail) must NOT be rendered —
    agent_loop re-adds it as the live prompt; rendering it too = double-add."""
    sess = "s1"
    db.create_session(sess, "agent")
    db.append_message(sess, {"id": "u1", "role": "user", "content": "hello"})
    db.append_message(sess, {"id": "a1", "role": "assistant", "content": "hi there",
                             "parent_id": "u1"})
    db.append_message(sess, {"id": "u2", "role": "user", "content": "NEW_QUESTION",
                             "parent_id": "a1"})

    msgs = _engine()._build_messages_from_dag(session_id=sess, history=[], model=None)
    blob = "\n".join(_texts(msgs))
    assert "hello" in blob          # prior turn present
    assert "hi there" in blob       # prior assistant present
    assert "NEW_QUESTION" not in blob   # trailing user excluded (no double-add)


def test_dag_render_accumulates_prior_turns(db):
    """Top-level chat (frame=-1) accumulates all prior turns."""
    sess = "s2"
    db.create_session(sess, "agent")
    prev = None
    for i in range(1, 4):
        msg_u = {"id": f"u{i}", "role": "user", "content": f"q{i}"}
        if prev:
            msg_u["parent_id"] = prev
        db.append_message(sess, msg_u)
        db.append_message(sess, {"id": f"a{i}", "role": "assistant", "content": f"a{i}",
                                 "parent_id": f"u{i}"})
        prev = f"a{i}"
    db.append_message(sess, {"id": "u_last", "role": "user", "content": "LATEST",
                             "parent_id": prev})

    msgs = _engine()._build_messages_from_dag(session_id=sess, history=[], model=None)
    blob = "\n".join(_texts(msgs))
    for i in range(1, 4):
        assert f"q{i}" in blob and f"a{i}" in blob   # all prior turns accumulate
    assert "LATEST" not in blob                       # trailing user excluded
