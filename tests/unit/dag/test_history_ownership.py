"""Owned child change sets participate in parent history operations."""
from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent.history_ownership import owned_change_set_closure
from openprogram.agent.internals._revert import reapply_turn, revert_turn
from openprogram.agent.job.store import save_job
from openprogram.agent.job.runner import _mirror_linked_job_to_caller
from openprogram.agent.job.types import Job, JobStatus
from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    value = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", value,
        raising=False,
    )
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    value.create_session("s", "main", title="ownership")
    value.append_message("s", {"id": "u1", "role": "user", "content": "edit"})
    value.append_message("s", {
        "id": "a1", "role": "assistant", "content": "done", "predecessor": "u1",
    })
    return value


def _job(**overrides) -> Job:
    values = {
        "id": "j_owned",
        "parent_session_id": "s",
        "prompt": "child",
        "agent_id": "child",
        "parent_msg_id": "a1",
        "caller_msg_id": "a1",
        "origin_turn_id": "a1",
        "creates_agent": True,
        "relation": "owned",
        "status": JobStatus.COMPLETED,
        "head_id": "child-a",
    }
    values.update(overrides)
    return Job(**values)


def test_running_owned_child_blocks_parent_history(store):
    save_job("s", _job(status=JobStatus.RUNNING))

    closure = owned_change_set_closure("s", ["a1"])

    assert closure["status"] == "blocked"
    assert closure["blockers"][0]["job_id"] == "j_owned"


def test_legacy_job_without_explicit_origin_is_never_owned(store):
    save_job("s", _job(origin_turn_id=None))

    closure = owned_change_set_closure("s", ["a1"])

    assert closure["owned_turn_ids"] == []
    assert closure["linked"][0]["job_id"] == "j_owned"


def test_running_owned_child_blocks_revert_without_file_writes(store, tmp_path):
    target = tmp_path / "blocked.py"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir("s"))
    journal.backup_before_edit("a1", str(target))
    target.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit("a1", str(target), operation="edit")
    save_job("s", _job(status=JobStatus.RUNNING))

    result = revert_turn("s", "a1", idempotency_key="blocked-owned")

    assert result["status"] == "blocked"
    assert result["error"] == "owned_actor_running"
    assert target.read_text(encoding="utf-8") == "after\n"


def test_completed_owned_child_is_included_but_linked_and_worktree_are_not(store):
    save_job("s", _job())
    save_job("s", _job(
        id="j_linked", relation="linked", creates_agent=False, head_id="peer-a",
    ))
    save_job("s", _job(
        id="j_worktree", relation="worktree", worktree_id="wt-1", head_id="wt-a",
    ))

    closure = owned_change_set_closure("s", ["a1"])

    assert closure["status"] == "ready"
    assert closure["owned_turn_ids"] == ["child-a"]
    assert {item["job_id"] for item in closure["linked"]} == {
        "j_linked", "j_worktree",
    }


def test_cross_session_link_is_mirrored_to_origin_for_impact_reporting(store):
    store.create_session("peer", "main", title="peer")
    job = _job(
        id="j_cross",
        parent_session_id="peer",
        relation="linked",
        creates_agent=False,
        caller_session_id="s",
        status=JobStatus.RUNNING,
        head_id=None,
    )
    save_job("peer", job)

    _mirror_linked_job_to_caller(job)
    closure = owned_change_set_closure("s", ["a1"])

    assert closure["status"] == "blocked"
    assert closure["linked"][0]["job_id"] == "j_cross"
    assert closure["blockers"][0]["job_id"] == "j_cross"


def test_parent_revert_restores_owned_child_only(store, tmp_path):
    store.spawn_branch(
        "s", "a1", source="agent_spawn", node_id="child-u",
        prompt="child", register_head=False,
    )
    store.append_message("s", {
        "id": "child-a", "role": "assistant", "content": "child done",
        "predecessor": "child-u",
    })
    parent_file = tmp_path / "parent.py"
    child_file = tmp_path / "child.py"
    linked_file = tmp_path / "linked.py"
    worktree_file = tmp_path / "isolated-worktree.py"
    journal = CheckpointStore(store._session_dir("s"))
    for turn_id, path in (
        ("a1", parent_file),
        ("child-a", child_file),
        ("peer-a", linked_file),
        ("wt-a", worktree_file),
    ):
        path.write_text("before\n", encoding="utf-8")
        journal.backup_before_edit(turn_id, str(path))
        path.write_text("after\n", encoding="utf-8")
        journal.commit_after_edit(turn_id, str(path), operation="edit")
    save_job("s", _job())
    save_job("s", _job(
        id="j_linked", relation="linked", creates_agent=False, head_id="peer-a",
    ))
    save_job("s", _job(
        id="j_worktree", relation="worktree", worktree_id="wt-1", head_id="wt-a",
    ))

    from openprogram.webui.ws_actions.turn_files import _turn_scope

    scope = _turn_scope("s", "a1")
    assert {row["path"] for row in scope["files"]} == {
        str(parent_file), str(child_file),
    }
    child_row = next(row for row in scope["files"] if row["path"] == str(child_file))
    assert child_row["producer_turn_id"] == "child-a"
    assert child_row["actor_id"] == "main"  # no runner stamp in this fixture
    assert {impact["job_id"] for impact in scope["linked_impacts"]} == {
        "j_linked", "j_worktree",
    }

    result = revert_turn("s", "a1", idempotency_key="owned-parent")

    assert result["status"] == "committed"
    assert result["owned_turn_ids"] == ["child-a"]
    assert parent_file.read_text(encoding="utf-8") == "before\n"
    assert child_file.read_text(encoding="utf-8") == "before\n"
    assert linked_file.read_text(encoding="utf-8") == "after\n"
    assert worktree_file.read_text(encoding="utf-8") == "after\n"

    from openprogram.webui.ws_actions.turn_files import _branch_scope

    reverted_scope = _branch_scope("s")
    assert str(parent_file) not in {row["path"] for row in reverted_scope["files"]}
    assert str(child_file) not in {row["path"] for row in reverted_scope["files"]}

    reapplied = reapply_turn("s", "a1", idempotency_key="owned-parent-redo")

    assert reapplied["status"] == "committed"
    assert reapplied["owned_turn_ids"] == ["child-a"]
    assert parent_file.read_text(encoding="utf-8") == "after\n"
    assert child_file.read_text(encoding="utf-8") == "after\n"
    assert linked_file.read_text(encoding="utf-8") == "after\n"
    assert worktree_file.read_text(encoding="utf-8") == "after\n"
    reapplied_scope = _branch_scope("s")
    assert {str(parent_file), str(child_file)} <= {
        row["path"] for row in reapplied_scope["files"]
    }


def test_nested_owned_same_path_revert_and_reapply_follow_workspace_order(
    store, tmp_path,
):
    store.spawn_branch(
        "s", "a1", source="agent_spawn", node_id="child-u",
        prompt="child", register_head=False,
    )
    store.append_message("s", {
        "id": "child-a", "role": "assistant", "content": "child done",
        "predecessor": "child-u",
    })
    store.spawn_branch(
        "s", "child-a", source="agent_spawn", node_id="grand-u",
        prompt="grand", register_head=False,
    )
    store.append_message("s", {
        "id": "grand-a", "role": "assistant", "content": "grand done",
        "predecessor": "grand-u",
    })
    target = tmp_path / "nested.py"
    target.write_text("0\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir("s"))
    for turn_id, value in (("a1", "1\n"), ("child-a", "2\n"), ("grand-a", "3\n")):
        journal.backup_before_edit(turn_id, str(target))
        target.write_text(value, encoding="utf-8")
        journal.commit_after_edit(turn_id, str(target), operation="edit")
    save_job("s", _job(head_id="child-a"))
    save_job("s", _job(
        id="j-grand", origin_turn_id="child-a", parent_msg_id="child-a",
        caller_msg_id="child-a", head_id="grand-a",
    ))

    closure = owned_change_set_closure("s", ["a1"])
    reverted = revert_turn("s", "a1", idempotency_key="nested-revert")

    assert closure["owned_turn_ids"] == ["grand-a", "child-a"]
    assert reverted["status"] == "committed"
    assert target.read_text(encoding="utf-8") == "0\n"

    reapplied = reapply_turn("s", "a1", idempotency_key="nested-reapply")

    assert reapplied["status"] == "committed"
    assert target.read_text(encoding="utf-8") == "3\n"
