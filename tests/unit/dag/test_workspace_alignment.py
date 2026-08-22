"""Conversation checkout requires an explicit workspace decision."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent.workspace_alignment import (
    get_workspace_alignment,
    mark_conversation_checkout,
    plan_branch_workspace_restore,
    restore_branch_workspace,
)
from openprogram.agent.job.store import save_job
from openprogram.agent.job.types import Job, JobStatus
from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.fixture
def branch_workspace(tmp_path: Path, monkeypatch):
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", store,
        raising=False,
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    store.create_session("s", "main", title="alignment")
    store.append_message("s", {"id": "u0", "role": "user", "content": "base"})
    store.append_message("s", {
        "id": "a0", "role": "assistant", "content": "base", "predecessor": "u0",
    })
    store.append_message("s", {
        "id": "u1", "role": "user", "content": "source", "predecessor": "a0",
    })
    store.append_message("s", {
        "id": "a1", "role": "assistant", "content": "source", "predecessor": "u1",
    })
    store.append_message("s", {
        "id": "f1", "role": "user", "content": "target", "predecessor": "a0",
    })
    store.append_message("s", {
        "id": "fa", "role": "assistant", "content": "target", "predecessor": "f1",
    })
    path = tmp_path / "workspace.py"
    path.write_text("base\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir("s"))
    journal.backup_before_edit("a1", str(path))
    path.write_text("source\n", encoding="utf-8")
    journal.commit_after_edit("a1", str(path), operation="edit")
    path.write_text("base\n", encoding="utf-8")
    journal.backup_before_edit("fa", str(path))
    path.write_text("target\n", encoding="utf-8")
    journal.commit_after_edit("fa", str(path), operation="edit")
    path.write_text("source\n", encoding="utf-8")
    store.set_head("s", "fa")
    mark_conversation_checkout("s", "a1", "fa", store=store)
    return store, path


def test_restore_branch_code_materializes_target_without_moving_head(branch_workspace):
    store, path = branch_workspace

    plan = plan_branch_workspace_restore("s", store=store)
    result = restore_branch_workspace(
        "s", store=store, idempotency_key="restore-target",
    )

    assert plan["status"] == "ready"
    assert len(plan["actions"]) == 1
    assert result["status"] == "committed"
    assert path.read_text(encoding="utf-8") == "target\n"
    assert store.get_session("s")["head_id"] == "fa"
    assert store.get_session("s")["workspace_alignment"]["status"] == "aligned"
    assert store.get_session("s")["workspace_alignment"]["decision"] == "restore_branch_code"


def test_restore_branch_code_blocks_external_workspace_change(branch_workspace):
    store, path = branch_workspace
    path.write_text("external\n", encoding="utf-8")

    result = restore_branch_workspace(
        "s", store=store, idempotency_key="restore-conflict",
    )

    assert result["status"] == "blocked"
    assert path.read_text(encoding="utf-8") == "external\n"
    assert store.get_session("s")["workspace_alignment"]["status"] == "mismatch"


def test_consecutive_checkouts_keep_the_materialized_source_head(branch_workspace):
    store, _path = branch_workspace

    mark_conversation_checkout("s", "fa", "a0", store=store)
    alignment = get_workspace_alignment("s", store=store)

    assert alignment["status"] == "mismatch"
    assert alignment["source_head_id"] == "a1"
    assert alignment["target_head_id"] == "a0"


def test_checkout_back_to_materialized_branch_clears_mismatch(branch_workspace):
    store, _path = branch_workspace

    alignment = mark_conversation_checkout("s", "fa", "a1", store=store)

    assert alignment["status"] == "aligned"
    assert alignment["decision"] == "return_to_workspace_branch"


def test_restore_branch_projection_includes_owned_child_changes(branch_workspace):
    store, path = branch_workspace
    store.spawn_branch(
        "s", "a1", source="agent_spawn", node_id="child-u",
        prompt="child", register_head=False,
    )
    store.append_message("s", {
        "id": "child-a", "role": "assistant", "content": "child done",
        "predecessor": "child-u",
    })
    journal = CheckpointStore(store._session_dir("s"))
    journal.backup_before_edit("child-a", str(path))
    path.write_text("source-child\n", encoding="utf-8")
    journal.commit_after_edit("child-a", str(path), operation="edit")
    save_job("s", Job(
        id="j-child",
        parent_session_id="s",
        prompt="child",
        agent_id="child",
        parent_msg_id="a1",
        caller_msg_id="a1",
        origin_turn_id="a1",
        creates_agent=True,
        relation="owned",
        status=JobStatus.COMPLETED,
        head_id="child-a",
    ))

    result = restore_branch_workspace(
        "s", store=store, idempotency_key="restore-owned-source",
    )

    assert result["status"] == "committed"
    assert path.read_text(encoding="utf-8") == "target\n"


def test_branch_projection_uses_mutation_order_for_owned_siblings(branch_workspace):
    store, path = branch_workspace
    # Completion/node order is c2 then c1; actual writes are c1 then c2.
    for prefix in ("c2", "c1"):
        store.spawn_branch(
            "s", "a1", source="agent_spawn", node_id=f"{prefix}-u",
            prompt=prefix, register_head=False,
        )
        store.append_message("s", {
            "id": f"{prefix}-a", "role": "assistant", "content": "done",
            "predecessor": f"{prefix}-u",
        })
    journal = CheckpointStore(store._session_dir("s"))
    for turn_id, value in (("c1-a", "source-1\n"), ("c2-a", "source-2\n")):
        journal.backup_before_edit(turn_id, str(path))
        path.write_text(value, encoding="utf-8")
        journal.commit_after_edit(turn_id, str(path), operation="edit")
    for prefix in ("c1", "c2"):
        save_job("s", Job(
            id=f"j-{prefix}", parent_session_id="s", prompt=prefix,
            agent_id=prefix, parent_msg_id="a1", caller_msg_id="a1",
            origin_turn_id="a1", creates_agent=True, relation="owned",
            status=JobStatus.COMPLETED, head_id=f"{prefix}-a",
        ))

    result = restore_branch_workspace(
        "s", store=store, idempotency_key="restore-owned-siblings",
    )

    assert result["status"] == "committed"
    assert path.read_text(encoding="utf-8") == "target\n"


def test_concurrent_head_move_rolls_back_branch_workspace_restore(
    branch_workspace, monkeypatch,
):
    store, path = branch_workspace
    original = CheckpointStore._apply_state
    moved = False

    def apply_then_move_head(self, *args, **kwargs):
        nonlocal moved
        result = original(self, *args, **kwargs)
        if not moved:
            moved = True
            store.set_head("s", "a0")
        return result

    monkeypatch.setattr(CheckpointStore, "_apply_state", apply_then_move_head)

    result = restore_branch_workspace(
        "s", store=store, idempotency_key="restore-head-race",
    )

    assert result["status"] == "rolled_back"
    assert result["error"] == "stale_head"
    assert path.read_text(encoding="utf-8") == "source\n"
    assert store.get_session("s")["head_id"] == "a0"
    assert store.get_session("s")["workspace_alignment"]["status"] == "mismatch"


def test_external_write_during_final_cas_is_not_reported_committed(
    branch_workspace, monkeypatch,
):
    store, path = branch_workspace
    original = store.compare_and_set_head
    injected = False

    def write_before_cas(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            path.write_text("external\n", encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "compare_and_set_head", write_before_cas)

    result = restore_branch_workspace(
        "s", store=store, idempotency_key="restore-external-race",
    )

    assert result["status"] == "recovery_required"
    assert path.read_text(encoding="utf-8") == "external\n"
    assert store.get_session("s")["workspace_alignment"]["status"] == "mismatch"


def test_branch_restore_exact_idempotency_replay_survives_aligned_state(
    branch_workspace,
):
    store, path = branch_workspace

    first = restore_branch_workspace(
        "s", store=store, idempotency_key="stable-replay",
        source_head_id="a1", target_head_id="fa",
    )
    replay = restore_branch_workspace(
        "s", store=store, idempotency_key="stable-replay",
        source_head_id="a1", target_head_id="fa",
    )
    conflict = restore_branch_workspace(
        "s", store=store, idempotency_key="stable-replay",
        source_head_id="a1", target_head_id="a0",
    )

    assert first["status"] == "committed"
    assert replay["status"] == "committed"
    assert replay["replayed"] is True
    assert conflict["status"] == "idempotency_conflict"
    assert path.read_text(encoding="utf-8") == "target\n"
