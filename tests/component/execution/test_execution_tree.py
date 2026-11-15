"""The executions one run is made of, rooted at the execution it started.

A run's usage and transcript cover its own execution, the executions below
it (sub-agent Jobs, theirs in turn) and the turn a Worker restart continues
it as; never another branch of the same conversation.
"""
from __future__ import annotations

from openprogram.runtime.execution import restart
from openprogram.runtime.execution.execution_tree import (
    RESTART_REQUESTED,
    continuation_key,
    execution_tree,
)
from openprogram.runtime.execution.model import CapabilitySet
from openprogram.runtime.execution.store import ExecutionStore

SESSION = "local_tree"


def _store(tmp_path) -> ExecutionStore:
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    store.create_revision(revision_id="r1", manifest={"entrypoint": "agent"})
    return store


def _admit(store, execution_id=None, parent=None, **extra):
    return store.admit_execution(
        execution_id=execution_id, session_id=SESSION, revision_id="r1", input_ref="i",
        input_hash=f"h-{execution_id or extra.get('admission_key')}", entrypoint="agent",
        trusted_actor={"subject": "u"}, config_snapshot_ref="c",
        capabilities=CapabilitySet(pause=False, step=False, steer=False),
        parent_execution_id=parent, **extra,
    )


def test_the_tree_holds_the_run_and_everything_below_it_not_its_siblings(tmp_path):
    store = _store(tmp_path)
    _admit(store, "run")
    _admit(store, "child", parent="run")
    _admit(store, "grandchild", parent="child")
    # Another branch of the same conversation, running beside the run.
    _admit(store, "sibling")
    _admit(store, "siblings_child", parent="sibling")

    assert execution_tree("run", store=store) == {"run", "child", "grandchild"}
    assert execution_tree("sibling", store=store) == {"sibling", "siblings_child"}


def test_the_turn_a_restart_continues_the_run_as_belongs_to_it(tmp_path):
    store = _store(tmp_path)
    interrupted = _admit(store, "run")
    with store._transaction() as connection:
        restart.record_intent(store, connection, interrupted, interrupted_at=1.0, seconds=-1,
                              mode="new_turn")
    [intent] = [e for e in store.list_events("run") if e.kind == RESTART_REQUESTED]
    continued = _admit(store, admission_key=continuation_key("run", intent.sequence))
    _admit(store, "continued_child", parent=continued.execution_id)

    assert execution_tree("run", store=store) == {
        "run", continued.execution_id, "continued_child"}


def test_an_execution_the_store_does_not_know_is_its_own_tree(tmp_path):
    assert execution_tree("unknown", store=_store(tmp_path)) == {"unknown"}


def test_the_restart_intent_kind_is_the_one_the_restart_writes():
    assert restart._REQUEST == RESTART_REQUESTED
