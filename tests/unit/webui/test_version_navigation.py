"""The ``< N/M >`` version arrows on real store rows."""
from __future__ import annotations

import pytest

from openprogram.interfaces.server.persistence import aggregate_tool_messages
from openprogram.runtime.context.git import deepest_leaf, sibling_navigation_index
from openprogram.state.store import SessionNodeWriter, SessionStore
from openprogram.state.store.session.nodes import ROLE_CODE, Call

SESSION = "session-1"


@pytest.fixture
def store(tmp_path, monkeypatch):
    sessions = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: sessions)
    sessions.create_session(SESSION, "main")
    return sessions


def add(store, row_id, role, predecessor, **fields):
    store.append_message(SESSION, {"id": row_id, "role": role, "content": row_id, "predecessor": predecessor, **fields})
    return row_id


def navigation(store, *ids):
    return sibling_navigation_index(aggregate_tool_messages(store.get_messages(SESSION)), target_ids=set(ids))


def test_a_compaction_summary_is_not_a_version_of_the_first_row_it_covers(store):
    from openprogram.runtime.context.persistence import default_persister

    add(store, "u0", "user", "ROOT")
    add(store, "a0", "assistant", "u0")
    add(store, "u1", "user", "a0")
    add(store, "a1", "assistant", "u1")
    add(store, "u2", "user", "a1")
    history = [m for m in store.get_messages(SESSION) if m["id"] in ("u1", "a1", "u2")]
    summary = default_persister.insert_summary_node(SESSION, summary_text="recap", cut_idx=2, history=history)
    assert summary

    assert navigation(store, "u1", summary) == {
        "u1": (1, 1, None, None),
        summary: (1, 1, None, None),
    }


def two_versions(store):
    """``u1`` and its edited sibling ``u1b``; the arrows on ``u1`` lead to ``u1b``'s branch."""
    add(store, "u0", "user", "ROOT")
    add(store, "a0", "assistant", "u0")
    add(store, "u1", "user", "a0")
    add(store, "a1", "assistant", "u1")
    add(store, "u1b", "user", "a0")
    return add(store, "a1b", "assistant", "u1b")


def attach(store, row_id, predecessor):
    return add(store, row_id, "assistant", predecessor, function="attach", display="runtime", attach={"job_id": "j"})


def arrow_from_u1(store):
    rows = aggregate_tool_messages(store.get_messages(SESSION))
    [_, _, _, following] = sibling_navigation_index(rows, target_ids={"u1"})["u1"]
    # The unindexed walk behind function-run arrows must agree.
    assert deepest_leaf(rows, "u1b") == following
    return following


def test_the_version_arrow_never_lands_on_an_attach_row(store):
    reply = two_versions(store)
    attach(store, "att", reply)

    assert arrow_from_u1(store) == reply


def test_the_version_arrow_never_lands_on_a_runtime_placeholder(store):
    reply = two_versions(store)
    add(store, "fn", "assistant", reply, type="status", display="runtime", status="done")

    assert arrow_from_u1(store) == reply


def test_the_version_arrow_lands_past_a_top_level_run(store):
    reply = two_versions(store)
    SessionNodeWriter(store, SESSION).append(Call(
        id="run", role=ROLE_CODE, name="report", output="out", predecessor=reply, metadata={"display": "runtime"},
    ))

    assert arrow_from_u1(store) == "run"


def test_the_version_arrow_lands_past_a_job_followup_prompt(store):
    reply = two_versions(store)
    attach(store, "att", reply)
    add(store, "follow", "user", reply, display="runtime", source="job_followup")
    add(store, "follow_reply", "assistant", "follow")

    assert arrow_from_u1(store) == "follow_reply"


def test_a_branch_that_continues_past_an_attach_row_stays_reachable(store):
    """An arrow used to be able to stand on an attach row, so the next message hung off it."""
    reply = two_versions(store)
    attach(store, "att", reply)
    add(store, "u2", "user", "att")
    add(store, "a2", "assistant", "u2")

    assert arrow_from_u1(store) == "a2"


def test_an_attach_row_never_ends_a_walk_through_another_side_row(store):
    reply = two_versions(store)
    attach(store, "att", reply)
    attach(store, "att2", "att")

    assert arrow_from_u1(store) == reply
