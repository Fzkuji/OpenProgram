"""``/context-range`` coverage flags.

The DAG's coverage mode (dag/rendering.md §8) paints the graph from
this payload: a node is bright when it is in the covered set, and it
takes a ▤ corner mark when the result was spilled to a file. The
frontend must not derive either — they are pipeline facts, so the
endpoint reads them from what the render pass reads (``metadata.spilled``).
``aged`` stays in the payload and is always false: the render never
shortens an old result; only compaction replaces it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.state.store.session.nodes import Call, ROLE_USER, ROLE_LLM, ROLE_CODE
from openprogram.state.store import SessionNodeWriter, SessionStore
from openprogram.interfaces.server.routes.files.tree import _coverage_nodes


@pytest.fixture
def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions-git")
    s.create_session("s1", agent_id="main")
    return s


def _turns(store: SessionStore, n: int) -> list[str]:
    """n turns of user → llm → tool. Returns the node ids in order."""
    shim = SessionNodeWriter(store, "s1")
    ids: list[str] = []
    prev = None
    for i in range(n):
        u = Call(id=f"u{i}", role=ROLE_USER, output=f"q{i}", predecessor=prev)
        llm = Call(id=f"a{i}", role=ROLE_LLM, output=f"r{i}", predecessor=u.id)
        code = Call(
            id=f"c{i}", role=ROLE_CODE, name="grep", caller=llm.id,
            input={"pattern": f"p{i}"}, output=f"result {i}\n" * 20,
            metadata={"expose": "full", "tool_call_id": f"tc{i}"},
        )
        for node in (u, llm, code):
            shim.append(node)
            ids.append(node.id)
        prev = llm.id
    return ids


def test_every_covered_node_is_in_context(store):
    ids = _turns(store, 2)
    rows = _coverage_nodes(store, "s1", ids)
    assert [r["node_id"] for r in rows] == ids
    assert all(r["in_context"] for r in rows)


def test_old_code_nodes_are_never_aged(store):
    # The render sends an old result as its turn did; nothing is stubbed.
    ids = _turns(store, 6)
    rows = {r["node_id"]: r for r in _coverage_nodes(store, "s1", ids)}
    assert not any(row["aged"] for row in rows.values())


def test_spilled_metadata_surfaces(store):
    ids = _turns(store, 1)
    shim = SessionNodeWriter(store, "s1")
    shim.append(Call(
        id="big", role=ROLE_CODE, name="bash", caller="a0",
        input={"cmd": "cat huge.log"}, output="[spilled]",
        metadata={"expose": "full", "tool_call_id": "tcbig",
                  "spilled": {"path": "large_nodes/big.txt", "bytes": 512000}},
    ))
    rows = {r["node_id"]: r for r in _coverage_nodes(store, "s1", ids + ["big"])}
    assert rows["big"]["spilled"]
    assert not rows["c0"]["spilled"]


def test_empty_and_unloadable_stay_quiet(store):
    assert _coverage_nodes(store, "s1", []) == []
    # A graph that can't be loaded must not 500 the highlight: unknown
    # ids come back in-context with both degradation flags off.
    rows = _coverage_nodes(store, "no-such-session", ["ghost"])
    assert rows == [
        {"node_id": "ghost", "in_context": True, "aged": False, "spilled": False},
    ]


def test_context_range_lists_the_calls_under_each_turn(store):
    # The predecessor walk only sees turns; the calls a reply made hang
    # off it by caller. The next request reads them, so the range must
    # list them — right after their reply — or every opened call draws
    # as out of context.
    from openprogram.interfaces.server.routes.files.tree import _with_code_nodes

    _turns(store, 2)
    assert _with_code_nodes(store, "s1", None, ["u0", "a0", "u1", "a1"]) == [
        "u0", "a0", "c0", "u1", "a1", "c1",
    ]
    # A turn the compaction view left out keeps its calls out with it.
    assert _with_code_nodes(store, "s1", "a1", ["u1", "a1"]) == ["u1", "a1", "c1"]
