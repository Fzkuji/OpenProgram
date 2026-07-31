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
                             "predecessor": "u1"})
    db.append_message(sess, {"id": "u2", "role": "user", "content": "NEW_QUESTION",
                             "predecessor": "a1"})

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
            msg_u["predecessor"] = prev
        db.append_message(sess, msg_u)
        db.append_message(sess, {"id": f"a{i}", "role": "assistant", "content": f"a{i}",
                                 "predecessor": f"u{i}"})
        prev = f"a{i}"
    db.append_message(sess, {"id": "u_last", "role": "user", "content": "LATEST",
                             "predecessor": prev})

    msgs = _engine()._build_messages_from_dag(session_id=sess, history=[], model=None)
    blob = "\n".join(_texts(msgs))
    for i in range(1, 4):
        assert f"q{i}" in blob and f"a{i}" in blob   # all prior turns accumulate
    assert "LATEST" not in blob                       # trailing user excluded


# --- abandoned assistant turns (crashed stream) -----------------------
#
# A stream that dies mid-flight leaves an llm node with status "running"
# and empty output in the branch. It must not reach the model as an empty
# assistant message, and it must not make the NEXT user message collide
# with its seq.


def test_dag_render_skips_abandoned_running_llm_node(db):
    """Empty + status=running llm node is not rendered as an empty turn."""
    sess = "s_abandoned"
    db.create_session(sess, "agent")
    db.append_message(sess, {"id": "u1", "role": "user", "content": "first"})
    db.append_message(sess, {"id": "a1", "role": "assistant", "content": "answer one",
                             "predecessor": "u1"})
    # stream died here: no content, still marked running
    db.append_message(sess, {"id": "a_dead", "role": "assistant", "content": "",
                             "predecessor": "a1", "status": "running"})
    db.append_message(sess, {"id": "u2", "role": "user", "content": "second",
                             "predecessor": "a_dead"})
    db.append_message(sess, {"id": "a2", "role": "assistant", "content": "answer two",
                             "predecessor": "u2"})
    db.append_message(sess, {"id": "u3", "role": "user", "content": "TRAILING",
                             "predecessor": "a2"})

    msgs = _engine()._build_messages_from_dag(session_id=sess, history=[], model=None)
    texts = _texts(msgs)
    assert "answer one" in "\n".join(texts)
    assert "answer two" in "\n".join(texts)
    # No contentless assistant message survived the render.
    assert not [
        m for m, t in zip(msgs, texts)
        if m.role == "assistant" and not t.strip()
    ]


def test_dag_render_keeps_empty_llm_node_that_owns_tool_calls(db):
    """An assistant turn with no text but real tool calls must stay — its
    ToolCall entries would otherwise orphan the following tool_result."""
    sess = "s_toolonly"
    db.create_session(sess, "agent")
    db.append_message(sess, {"id": "u1", "role": "user", "content": "run it"})
    db.append_message(sess, {"id": "a1", "role": "assistant", "content": "",
                             "predecessor": "u1", "status": "running"})
    db.append_message(sess, {
        "id": "a1_t_call1", "role": "tool", "content": "tool output here",
        "extra": {"tool_use": {"tool_call_id": "call1", "name": "bash",
                               "arguments": {"command": "ls"}, "caller": "a1"}},
    })
    db.append_message(sess, {"id": "u2", "role": "user", "content": "TRAILING",
                             "predecessor": "a1"})

    msgs = _engine()._build_messages_from_dag(session_id=sess, history=[], model=None)
    # the tool result is rendered, and it is preceded by an assistant msg
    assert any(getattr(m, "tool_call_id", None) == "call1" for m in msgs)
    assert any(m.role == "assistant" for m in msgs)


def test_next_seq_skips_seq_held_by_abandoned_node(db):
    """The user message after a crashed turn gets its own seq, not a
    duplicate of the abandoned llm node's (the t_chain 0054 collision)."""
    sess = "s_seq"
    db.create_session(sess, "agent")
    db.append_message(sess, {"id": "u1", "role": "user", "content": "hi"})
    db.append_message(sess, {"id": "a_dead", "role": "assistant", "content": "",
                             "predecessor": "u1", "status": "running"})

    # Simulate the rebuild race that reset the counter below an occupied seq.
    _git, idx = db._open(sess)
    idx.next_seq = 0

    db.append_message(sess, {"id": "u2", "role": "user", "content": "next",
                             "predecessor": "a_dead"})

    seqs = [n.seq for n in idx.nodes_by_seq]
    assert len(seqs) == len(set(seqs)), f"duplicate seq allocated: {seqs}"
