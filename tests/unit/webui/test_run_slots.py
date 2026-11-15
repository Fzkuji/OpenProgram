"""One run-slot table per conversation branch, and the durable operation records.

The selected branch's slot and every other branch's slot are entries of one
table; ``_running_tasks`` and ``_branch_running_tasks`` are views of it. An
operation's record outlives its process only as a cancelled one, and a
conversation-wide operation (a rewind that restores files) makes new
executions of that conversation wait for it.
"""
from __future__ import annotations

import json
import os

import pytest

from openprogram.interfaces.server.server_runtime.run_slots import (
    SELECTED,
    BranchSlots,
    SelectedSlots,
    branch_running_refusal,
    busy_refusal,
    cancel_event,
    forget_operation,
    new_slot,
)
from openprogram.state.store.session import operations


def test_both_views_read_and_write_one_table():
    table: dict = {}
    selected, branches = SelectedSlots(table), BranchSlots(table)
    selected["s1"] = {"msg_id": "a"}
    branches[("s1", "branch_b")] = {"msg_id": "b"}
    branches[("s2", "branch_c")] = {"msg_id": "c"}

    assert table == {("s1", SELECTED): {"msg_id": "a"}, ("s1", "branch_b"): {"msg_id": "b"},
                     ("s2", "branch_c"): {"msg_id": "c"}}
    assert list(selected) == ["s1"] and len(selected) == 1
    assert sorted(branches) == [("s1", "branch_b"), ("s2", "branch_c")] and len(branches) == 2
    assert "s2" not in selected and ("s1", "branch_b") in branches
    # The branch view never reaches the selected slot.
    assert ("s1", SELECTED) not in branches
    with pytest.raises(KeyError):
        branches[("s1", SELECTED)] = {}
    assert selected.pop("s1")["msg_id"] == "a" and ("s1", SELECTED) not in table
    assert not selected and branches


def test_a_slot_is_an_operation_with_a_stop():
    slot = new_slot("compact_1", "compact")
    assert (slot["op_kind"], slot["op_version"], slot["op_scope"], slot["_reserved"]) == (
        "compact", 1, "branch", True)
    event = cancel_event(slot["op_id"])
    assert event is not None and not event.is_set()
    forget_operation("s", slot)
    assert cancel_event(slot["op_id"]) is None
    with pytest.raises(ValueError):
        new_slot("x", "dance")


def test_refusals_name_what_runs():
    refusal = busy_refusal("compact", branch_id="branch_b", op_id="op_1")
    assert (refusal["code"], refusal["kind"], refusal["branch_id"], refusal["op_id"]) == (
        "run_active", "compact", "branch_b", "op_1")
    assert refusal["error"] == "Context is being compacted on this branch; try again after it ends."
    waiting = branch_running_refusal([{"branch_id": "b", "branch_name": "nightly",
                                       "execution_id": "e", "status_version": 3,
                                       "reason_code": "wait_open"}])
    assert waiting["code"] == "branch_running" and waiting["blocking"]["execution_id"] == "e"
    assert waiting["error"].startswith("Branch “nightly” is waiting for an approval")


def test_a_restart_marks_running_operations_cancelled(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    operations.record_operation("s1", {"op_id": "op_live", "kind": "compact", "branch_id": None},
                                root=root)
    assert [op["op_id"] for op in operations.running_operations("s1", root=root)] == ["op_live"]

    # The process that ran it is gone: the next start marks it cancelled.
    path = operations.records_path("s1", root)
    data = json.loads(path.read_text())
    data["operations"]["op_live"]["pid"] = os.getpid() + 1_000_000
    path.write_text(json.dumps(data))
    monkeypatch.setattr("openprogram.host._platform.processes.process_alive", lambda pid: False)
    assert operations.sweep_interrupted(root, force=True) == 1
    assert operations.running_operations("s1", root=root) == []
    interrupted = operations.interrupted_operations("s1", root=root)
    assert [(op["op_id"], op["status"], op["reason_code"]) for op in interrupted] == [
        ("op_live", "cancelled", "restart")]
    # The next operation on that branch replaces the old record.
    operations.record_operation("s1", {"op_id": "op_next", "kind": "rewind", "branch_id": None},
                                root=root)
    operations.finish_operation("s1", "op_next", root=root)
    assert not path.exists()


def test_new_executions_wait_for_a_rewind_that_restores_files(tmp_path, monkeypatch):
    from openprogram.runtime.execution.model import CapabilitySet
    from openprogram.runtime.execution.store import ExecutionConflict, ExecutionStore

    root = tmp_path / "projects"
    monkeypatch.setattr(operations, "_root", lambda root_=None: root)
    store = ExecutionStore(tmp_path / "runtime" / "executions.sqlite3")
    store.create_revision(revision_id="rev_1", manifest={"entrypoint": "rev_1", "schema": 1})

    operations.record_operation("s1", {"op_id": "op_files", "kind": "rewind", "branch_id": None,
                                       "scope": "conversation"})
    assert operations.workspace_hold("s1")["op_id"] == "op_files"
    # Another conversation is not held.
    assert operations.workspace_hold("s2") is None

    waits: list[float] = []

    def rewind_ends_while_waiting(seconds):
        # The rewind ends while admission waits for it.
        waits.append(seconds)
        operations.finish_operation("s1", "op_files")

    monkeypatch.setattr(operations.time, "sleep", rewind_ends_while_waiting)
    record = store.create_execution(session_id="s1", revision_id="rev_1", execution_id="exec_1",
                                    run_id="run_1", capabilities=CapabilitySet())
    assert record.execution_id == "exec_1" and waits and operations.workspace_hold("s1") is None

    operations.record_operation("s1", {"op_id": "op_stuck", "kind": "rewind", "branch_id": None,
                                       "scope": "conversation"})
    monkeypatch.setattr(operations, "wait_for_workspace", lambda session_id, **_kw: False)
    with pytest.raises(ExecutionConflict):
        store.create_execution(session_id="s1", revision_id="rev_1", execution_id="exec_2",
                               run_id="run_2", capabilities=CapabilitySet())

