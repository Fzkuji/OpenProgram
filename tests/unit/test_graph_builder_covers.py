"""``build_session_graph`` exposes the compaction interval as node ids.

A summary node stores its interval as ``metadata.covers = [first_seq,
last_seq]`` (dag/overview.md §8). Seq orders the graph but never leaves
the store — every wire payload speaks ids. So the graph builder resolves
the interval once, on the way out, and the summary row carries
``covers_ids``: the ids the summary stands in for, in seq order, with
the summary itself excluded.

That single field is what the renderer draws the capsule from
(dag/rendering.md §9) — it folds exactly those nodes behind the pleats
and expands exactly those nodes as ghosts. No seq arithmetic in the
frontend, and no second endpoint to ask.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.context.persistence import SUMMARY_NODE_NAME
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


def _seq_of(store: SessionStore, sid: str, node_id: str) -> int:
    return next(n.seq for n in store.get_nodes(sid) if n.id == node_id)


def _row(graph: list[dict], node_id: str) -> dict:
    return next(r for r in graph if r["id"] == node_id)


def test_summary_row_carries_the_ids_it_covers(store):
    ids = _seed(store, "s1", 3)
    covered = ids[:4]                       # first two turns
    store.append_message("s1", {
        "id": "sum1", "role": "llm", "token_model": SUMMARY_NODE_NAME,
        "content": "[recap]", "predecessor": None,
        "extra": {"covers": [_seq_of(store, "s1", covered[0]),
                             _seq_of(store, "s1", covered[-1])]},
    })

    graph = build_session_graph("s1", ids[-1])

    assert _row(graph, "sum1")["covers_ids"] == covered
    # The interval belongs to the summary alone — a covered node does not
    # inherit it, or the renderer would fold the fold.
    assert "covers_ids" not in _row(graph, covered[0])
    assert "covers_ids" not in _row(graph, ids[-1])


def test_a_summary_never_covers_itself(store):
    """Its own seq sorts just inside the range it names (the persister
    orders it immediately before the first covered node), so an id-space
    resolution that forgot to exclude it would fold the capsule away."""
    ids = _seed(store, "s1", 2)
    store.append_message("s1", {
        "id": "sum1", "role": "llm", "token_model": SUMMARY_NODE_NAME,
        "content": "[recap]", "predecessor": None,
        # A range wide enough to swallow every node written so far.
        "extra": {"covers": [0, 9999]},
    })

    covers = _row(build_session_graph("s1", ids[-1]), "sum1")["covers_ids"]

    assert "sum1" not in covers
    assert set(covers) >= set(ids)


def test_covers_skips_dead_fork_siblings_in_the_interval(store):
    """Compaction summarises one chain. A retried/abandoned branch whose
    seqs fall inside the interval was never part of that context, so the
    capsule must not fold it — the seq sweep is restricted to the head's
    predecessor chain (plus caller subtrees of covered turns)."""
    ids = _seed(store, "s1", 3)
    # Dead fork off the first reply — same era, other branch.
    store.append_message("s1", {"id": "fu", "role": "user",
                                "content": "alt", "predecessor": ids[1]})
    store.append_message("s1", {"id": "fa", "role": "assistant",
                                "content": "alt-r", "predecessor": "fu"})
    lo = _seq_of(store, "s1", ids[0])
    hi = _seq_of(store, "s1", "fa")     # interval spans the fork too
    store.append_message("s1", {
        "id": "sum1", "role": "llm", "token_model": SUMMARY_NODE_NAME,
        "content": "[recap]", "predecessor": None,
        "extra": {"covers": [lo, hi]},
    })

    covers = _row(build_session_graph("s1", ids[-1]), "sum1")["covers_ids"]

    assert "fu" not in covers and "fa" not in covers
    assert set(covers) == set(ids)


def test_uncompacted_sessions_carry_no_covers_field(store):
    ids = _seed(store, "s1", 2)
    graph = build_session_graph("s1", ids[-1])
    assert all("covers_ids" not in r for r in graph)
