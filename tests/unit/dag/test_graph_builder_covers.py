"""``build_session_graph`` exposes the compaction coverage as node ids.

A summary node stores the exact chain nodes it replaces as
``metadata.covers_ids``, written by the persister
(``context/persistence.py``). Seq intervals span sibling branches in a
DAG — a dead fork's seqs can fall inside ``[first_seq, last_seq]`` — so
ids are the only faithful record. The graph builder passes the list
through, adds the caller subtrees hanging off covered turns, and drops
ids that no longer exist.

That single field is what the renderer draws the capsule from
(dag/rendering.md §9) — it folds exactly those nodes behind the pleats
and expands exactly those nodes as ghosts. No seq arithmetic anywhere
past the store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.context.persistence import SUMMARY_NODE_NAME
from openprogram.context.nodes import Call, ROLE_CODE
from openprogram.store import SessionNodeWriter
from openprogram.agentic_programming.function import create_pending_call_node
from openprogram.store.session.session_store import SessionStore
from openprogram.webui.graph_builder import build_session_graph


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    st = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: st)
    return st


def _seed(store: SessionStore, sid: str, n: int) -> list[str]:
    """``n`` user/assistant pairs on one chain. Returns ids in order."""
    store.create_session(sid, "main", title="t")
    ids: list[str] = []
    prev = None
    for i in range(n):
        uid, aid = f"u{i}", f"a{i}"
        store.append_message(sid, {"id": uid, "role": "user",
                                   "content": f"q{i}", "predecessor": prev})
        store.append_message(sid, {"id": aid, "role": "assistant",
                                   "content": f"r{i}", "predecessor": uid})
        ids += [uid, aid]
        prev = aid
    return ids


def _summarize(store: SessionStore, sid: str, covered: list[str]) -> None:
    store.append_message(sid, {
        "id": "sum1", "role": "llm", "token_model": SUMMARY_NODE_NAME,
        "content": "[recap]", "predecessor": None,
        "extra": {"covers_ids": covered},
    })


def _row(graph: list[dict], node_id: str) -> dict:
    return next(r for r in graph if r["id"] == node_id)


def test_summary_row_carries_the_ids_it_covers(store):
    ids = _seed(store, "s1", 3)
    covered = ids[:4]                       # first two turns
    _summarize(store, "s1", covered)

    graph = build_session_graph("s1", ids[-1])

    assert _row(graph, "sum1")["covers_ids"] == covered
    # The coverage belongs to the summary alone — a covered node does not
    # inherit it, or the renderer would fold the fold.
    assert "covers_ids" not in _row(graph, covered[0])
    assert "covers_ids" not in _row(graph, ids[-1])


def test_covers_never_names_dead_fork_siblings(store):
    """The persister records the chain it summarised; a retried branch
    of the same era stays out of the capsule whatever HEAD points at."""
    ids = _seed(store, "s1", 3)
    # Dead fork off the first reply — same era, other branch.
    store.append_message("s1", {"id": "fu", "role": "user",
                                "content": "alt", "predecessor": ids[1]})
    store.append_message("s1", {"id": "fa", "role": "assistant",
                                "content": "alt-r", "predecessor": "fu"})
    _summarize(store, "s1", ids[:4])

    # Head on the dead fork must not change what the capsule folds.
    covers = _row(build_session_graph("s1", "fa"), "sum1")["covers_ids"]

    assert "fu" not in covers and "fa" not in covers
    assert set(covers) == set(ids[:4])


def test_covers_pulls_in_caller_subtrees_of_covered_turns(store):
    """A covered turn folds together with the tool calls it made."""
    ids = _seed(store, "s1", 2)
    store.append_message("s1", {"id": "tool1", "role": "code",
                                "content": "ran", "caller": ids[1]})
    _summarize(store, "s1", ids[:2])

    covers = _row(build_session_graph("s1", ids[-1]), "sum1")["covers_ids"]

    assert set(covers) == {ids[0], ids[1], "tool1"}


def test_a_summary_never_covers_itself(store):
    ids = _seed(store, "s1", 2)
    _summarize(store, "s1", ids + ["sum1", "no-such-node"])

    covers = _row(build_session_graph("s1", ids[-1]), "sum1")["covers_ids"]

    assert "sum1" not in covers
    assert "no-such-node" not in covers
    assert set(covers) == set(ids)


def test_only_the_active_rolling_summary_folds(store):
    """Compaction is a rolling summary: a second compact absorbs the
    first summary's text and ``extra_meta._last_summary_id`` points at
    the replacement. The old summary keeps its row but must not fold —
    two capsules over the same range fight for the survivors."""
    ids = _seed(store, "s1", 4)
    store.append_message("s1", {
        "id": "sum_old", "role": "llm", "token_model": SUMMARY_NODE_NAME,
        "content": "[recap 1]", "predecessor": None,
        "extra": {"covers_ids": ids[:4]},
    })
    store.append_message("s1", {
        "id": "sum_new", "role": "llm", "token_model": SUMMARY_NODE_NAME,
        "content": "[recap 2]", "predecessor": None,
        "extra": {"covers_ids": ids[:6]},
    })
    store.update_session("s1", extra_meta={"_last_summary_id": "sum_new"})

    graph = build_session_graph("s1", ids[-1])

    assert _row(graph, "sum_new")["covers_ids"] == ids[:6]
    old = _row(graph, "sum_old")
    assert "covers_ids" not in old
    assert old["superseded_summary"] is True


def test_uncompacted_sessions_carry_no_covers_field(store):
    ids = _seed(store, "s1", 2)
    graph = build_session_graph("s1", ids[-1])
    assert all("covers_ids" not in r for r in graph)


def test_structured_message_content_has_a_graph_preview(store):
    store.create_session("s1", "main", title="t")
    store.append_message("s1", {
        "id": "call1",
        "role": "code",
        "content": {"status": "completed", "result": ["saved", "report.md"]},
    })

    graph = build_session_graph("s1", "call1")

    assert _row(graph, "call1")["preview"] == (
        '{"status": "completed", "result": ["saved", "report.md"]}'
    )


def test_sequential_root_programs_stay_on_one_overview_lane(store):
    """Independent Program runs are ordered actions, not retry forks."""
    store.create_session("s1", "main", title="t")
    writer = SessionNodeWriter(store, "s1")
    writer.append(Call(
        id="ROOT", role="user", output="", metadata={"display": "root"},
    ))
    for i in range(3):
        writer.append(Call(
            id=f"program-{i}", role=ROLE_CODE, name="gui_agent",
            output="completed", predecessor="ROOT",
        ))

    graph = build_session_graph("s1", "program-2")
    rows = [_row(graph, f"program-{i}") for i in range(3)]

    assert [row["_lane"] for row in rows] == [0, 0, 0]
    assert [row["_depth"] for row in rows] == [1.0, 2.0, 3.0]


def test_persisted_program_retry_becomes_a_real_fork(store):
    store.create_session("s1", "main", title="t")
    writer = SessionNodeWriter(store, "s1")
    writer.append(Call(
        id="ROOT", role="user", output="", metadata={"display": "root"},
    ))
    original = create_pending_call_node(
        pending_id="run-1", function_name="gui_agent", arguments={},
        expose="io", forced_predecessor="ROOT", store=writer,
    )
    retry = create_pending_call_node(
        pending_id="retry", function_name="gui_agent", arguments={},
        expose="io", forced_predecessor="ROOT", retry_of="run-1",
        store=writer,
    )
    assert original is not None and retry is not None
    writer.append(original)
    writer.append(retry)

    graph = build_session_graph("s1", "retry")
    source_row = _row(graph, "run-1")
    retry_row = _row(graph, "retry")

    assert retry_row["retry_of"] == "run-1"
    assert retry_row["_lane"] != source_row["_lane"]
    assert retry_row["_depth"] == source_row["_depth"]
