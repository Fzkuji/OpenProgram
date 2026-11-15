"""A rewind retracts the tail of the current branch in place.

It opens no branch and no version: the branch keeps its record, and every
reader skips the retracted turns, which stay stored. Edit, retry and an
explicit fork are the operations that open a new branch.
"""
from types import SimpleNamespace

from tests.unit.dag.test_rewind_modes import Session, store, _append, _apply  # noqa: F401
from openprogram.runtime.conversation.rewind_marks import undo_rewind


def _record(store, sid="s"):
    row = store.get_session(sid)
    return row["active_branch_id"], row["branch_refs"]


def test_rewind_keeps_one_branch_record_and_opens_no_version(store):
    from openprogram.runtime.context.git import sibling_navigation_index

    session = Session(store, "s")
    session.turn_edits(); session.turn_edits(); session.turn_edits()
    store.set_branch_name("s", "a3", "Work")
    branch, refs = _record(store)
    config = refs[branch]["config"]

    _, result = _apply("s", "u2", "conversation")

    assert result["status"] == "committed"
    after, refs = _record(store)
    assert after == branch and set(refs) == {branch}
    assert refs[branch]["head_id"] == "a1" and refs[branch]["config"] == config
    assert [(row["branch_id"], row["head_msg_id"], row["name"]) for row in store.list_branches("s")] \
        == [(branch, "a1", "Work")]
    # The retracted turns stay stored, out of every listing.
    assert {"u2", "a2", "u3", "a3"} <= set(store._open("s")[1].nodes_by_id)
    assert [m["id"] for m in store.get_messages("s")] == ["u1", "a1"]
    assert store.search_messages("u3", session_id="s") == []

    # The next message is the branch's own continuation: one version, one record.
    _append(store, "s", "u4", "user", "a1")
    _append(store, "s", "a4", "assistant", "u4")
    store.set_head("s", "a4")
    navigation = sibling_navigation_index(store.get_messages("s"), target_ids={"u4", "a4"})
    assert {fields[:2] for fields in navigation.values()} <= {(1, 1), (0, 0)}
    assert set(_record(store)[1]) == {branch}
    assert [row["head_msg_id"] for row in store.list_branches("s")] == ["a4"]


def _summary(store, sid, node_id, covers, predecessor):
    store.append_message(sid, {
        "id": node_id, "role": "assistant", "content": f"summary of {covers}",
        "predecessor": predecessor, "covers_ids": covers,
    }, advance_head=False)


def test_a_summary_of_retracted_turns_is_retracted_and_the_one_before_applies(store):
    from openprogram.runtime.context.nodes import active_summary_for_chain
    from openprogram.state.store import SessionNodeWriter

    session = Session(store, "s")
    for _ in range(3):
        session.turn_edits()
    _summary(store, "s", "s1", ["u1", "a1"], "ROOT")
    _summary(store, "s", "s2", ["u1", "a1", "u2", "a2"], "ROOT")
    graph = SessionNodeWriter(store, "s").load()
    chain = ["u1", "a1", "u2", "a2", "u3", "a3"]
    assert active_summary_for_chain(graph, chain).id == "s2"

    _apply("s", "u2", "conversation")

    hidden = set(store.get_session("s")["rewind_hidden_nodes"])
    assert "s2" in hidden and "s1" not in hidden
    graph = SessionNodeWriter(store, "s").load()
    assert graph.retracted == frozenset(hidden)
    assert active_summary_for_chain(graph, ["u1", "a1"]).id == "s1"

    assert undo_rewind("s")["status"] == "committed"
    assert "s2" not in set(store.get_session("s")["rewind_hidden_nodes"])
    assert active_summary_for_chain(SessionNodeWriter(store, "s").load(), chain).id == "s2"


def test_the_context_timeline_offers_no_retracted_attempt():
    from openprogram.interfaces.server.ws_actions.context_commits import _find_fork_group

    nodes = {key: SimpleNamespace(predecessor=parent) for key, parent in
             {"u1": "ROOT", "a1": "u1", "u2": "a1", "a2": "u2", "u3": "a1", "a3": "u3"}.items()}
    index = SimpleNamespace(nodes_by_id=nodes, children_by_predecessor={
        "ROOT": ["u1"], "u1": ["a1"], "a1": ["u2", "u3"], "u2": ["a2"], "u3": ["a3"]},
        meta={"rewind_hidden_nodes": ["u2", "a2"]})
    assert _find_fork_group(index, "a3") == "single:a3"
    index.meta = {}
    assert _find_fork_group(index, "a3") == "fork:a1"
