"""Transactional multi-turn rewind contract."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from openprogram.store.session.session_store import SessionStore
from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.store.snapshot.checkpoint import manifest


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    value = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", value,
        raising=False,
    )
    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    return value


def _append(store: SessionStore, sid: str, msg_id: str, role: str, pred: str | None,
            *, display: str | None = None) -> None:
    metadata = {"display": display} if display else {}
    store.append_message(sid, {
        "id": msg_id,
        "role": role,
        "content": msg_id,
        "predecessor": pred,
        "metadata": metadata,
    })


def _seed_three_turns(
    store: SessionStore,
    sid: str,
    target: Path,
) -> tuple[list[str], CheckpointStore]:
    store.create_session(sid, "main", title="rewind transaction")
    _append(store, sid, "ROOT", "user", None, display="root")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("v0\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(sid))
    assistants: list[str] = []
    predecessor = "ROOT"
    for number in range(1, 4):
        user_id = f"u{number}"
        assistant_id = f"a{number}"
        _append(store, sid, user_id, "user", predecessor)
        journal.backup_before_edit(assistant_id, str(target))
        target.write_text(f"v{number}\n", encoding="utf-8")
        journal.commit_after_edit(assistant_id, str(target), operation="edit")
        _append(store, sid, assistant_id, "assistant", user_id)
        assistants.append(assistant_id)
        predecessor = assistant_id
    store.set_head(sid, assistants[-1])
    return assistants, journal


def test_rewind_folds_three_turns_into_one_target_state(store, tmp_path):
    from openprogram.agent._rewind import rewind_to

    target = tmp_path / "work" / "same.py"
    _seed_three_turns(store, "s-fold", target)

    result = rewind_to("s-fold", "u1", idempotency_key="rewind-fold")

    assert result["status"] == "committed"
    assert result["new_head_id"] == "ROOT"
    assert result["turns_reverted"] == 3
    assert result["total_restored_paths"] == [str(target)]
    assert target.read_text(encoding="utf-8") == "v0\n"
    assert store.get_session("s-fold")["head_id"] == "ROOT"
    _git, index = store._open("s-fold")
    refs = index.meta["branch_refs"]
    assert refs[result["source_branch_id"]]["head_id"] == "a3"
    assert refs[result["target_branch_id"]]["head_id"] == "ROOT"
    assert index.meta["active_branch_id"] == result["target_branch_id"]
    assert not any((node.metadata or {}).get("rewound") for node in index.all_nodes())


def test_file_apply_failure_rolls_back_and_does_not_move_head(
    store, tmp_path, monkeypatch,
):
    from openprogram.agent._rewind import rewind_to

    first = tmp_path / "work" / "first.py"
    assistants, journal = _seed_three_turns(store, "s-apply-fail", first)
    second = tmp_path / "work" / "second.py"
    second.write_text("before\n", encoding="utf-8")
    journal.backup_before_edit(assistants[-1], str(second))
    second.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit(assistants[-1], str(second), operation="edit")
    original_apply = CheckpointStore._apply_state

    def fail_second(self, path, state, backup_dir, transaction_id,
                    expected_current=None):
        if Path(path) == second:
            raise OSError("injected multi-turn apply failure")
        return original_apply(
            self, path, state, backup_dir, transaction_id, expected_current,
        )

    monkeypatch.setattr(CheckpointStore, "_apply_state", fail_second)

    result = rewind_to(
        "s-apply-fail", "u1", idempotency_key="rewind-apply-fail",
    )

    assert result["status"] == "rolled_back"
    assert result["new_head_id"] is None
    assert store.get_session("s-apply-fail")["head_id"] == assistants[-1]
    assert first.read_text(encoding="utf-8") == "v3\n"
    assert second.read_text(encoding="utf-8") == "after\n"


def test_stale_folded_plan_writes_no_planned_file_and_keeps_head(
    store, tmp_path, monkeypatch,
):
    from openprogram.agent._rewind import rewind_to

    target = tmp_path / "work" / "same.py"
    assistants, journal = _seed_three_turns(store, "s-stale", target)
    added = tmp_path / "work" / "late.py"
    added.write_text("late-before\n", encoding="utf-8")
    original_lock = CheckpointStore._workspace_lock
    injected = {"done": False}

    @contextmanager
    def add_receipt_before_replan(self, paths):
        with original_lock(self, paths):
            if not injected["done"]:
                injected["done"] = True
                journal.backup_before_edit(assistants[-1], str(added))
                added.write_text("late-after\n", encoding="utf-8")
                journal.commit_after_edit(
                    assistants[-1], str(added), operation="edit",
                )
            yield

    monkeypatch.setattr(CheckpointStore, "_workspace_lock", add_receipt_before_replan)

    result = rewind_to("s-stale", "u1", idempotency_key="rewind-stale")

    assert result["status"] == "aborted"
    assert result["error"] == "stale_plan"
    assert result["new_head_id"] is None
    assert store.get_session("s-stale")["head_id"] == assistants[-1]
    assert target.read_text(encoding="utf-8") == "v3\n"
    assert added.read_text(encoding="utf-8") == "late-after\n"


def test_incomplete_intent_recovers_committed_state(store, tmp_path):
    target = tmp_path / "work" / "same.py"
    assistants, journal = _seed_three_turns(store, "s-recover", target)
    plan = journal.plan_rewind_operation(list(reversed(assistants)))
    assert plan["status"] == "ready"
    target.write_text("v0\n", encoding="utf-8")
    assert store.compare_and_set_head("s-recover", assistants[-1], "ROOT")
    key = "recover-committed"
    intent_path = journal._rewind_intent_path(key)
    manifest.save(intent_path, {
        "version": 1,
        "transaction_id": "rewind_interrupted",
        "idempotency_key": key,
        "turn_ids": list(reversed(assistants)),
        "expected_head_id": assistants[-1],
        "target_head_id": "ROOT",
        "plan_hash": "unused-during-recovery",
        "status": "applying",
        "actions": plan["actions"],
        "conflicts": [],
        "unavailable": [],
        "error": None,
    })

    result = journal.apply_rewind_operation(
        list(reversed(assistants)),
        expected_head_id=assistants[-1],
        target_head_id="ROOT",
        get_head=lambda: store.get_session("s-recover")["head_id"],
        compare_and_set_head=lambda expected, new: store.compare_and_set_head(
            "s-recover", expected, new,
        ),
        idempotency_key=key,
    )

    assert result["status"] == "committed"
    assert result["transaction_id"] == "rewind_interrupted"
    assert result["new_head_id"] == "ROOT"
    assert result["restored_paths"] == [str(target)]


def test_head_compare_and_set_rejects_stale_source(store, tmp_path):
    target = tmp_path / "work" / "same.py"
    assistants, _journal = _seed_three_turns(store, "s-head-cas", target)

    assert not store.compare_and_set_head("s-head-cas", "a2", "ROOT")
    assert store.get_session("s-head-cas")["head_id"] == assistants[-1]


def test_rewind_keeps_sibling_nodes_and_points_out_of_active_plan(store, tmp_path):
    from openprogram.agent._rewind import list_rewind_points, rewind_to

    target = tmp_path / "work" / "same.py"
    _seed_three_turns(store, "s-sibling", target)
    _append(store, "s-sibling", "fork-u", "user", "a1")
    _append(store, "s-sibling", "fork-a", "assistant", "fork-u")
    store.set_head("s-sibling", "a3")

    assert "fork-u" not in {
        point["msg_id"] for point in list_rewind_points("s-sibling")
    }
    result = rewind_to("s-sibling", "u2", idempotency_key="sibling-rewind")

    assert result["status"] == "committed"
    _git, index = store._open("s-sibling")
    assert "fork-u" in index.nodes_by_id and "fork-a" in index.nodes_by_id
    assert not (index.nodes_by_id["fork-u"].metadata or {}).get("rewound")
    store.set_head("s-sibling", "fork-a")
    assert [
        message["id"] for message in store.get_branch("s-sibling", "fork-a")
    ] == ["ROOT", "u1", "a1", "fork-u", "fork-a"]


def test_parent_symlink_swap_cannot_redirect_rewind(store, tmp_path, monkeypatch):
    from openprogram.agent._rewind import plan_rewind, rewind_to

    parent = tmp_path / "work" / "safe"
    target = parent / "same.py"
    assistants, _journal = _seed_three_turns(store, "s-parent-swap", target)
    plan = plan_rewind("s-parent-swap", "u1")
    detached = tmp_path / "work" / "detached"
    outside = tmp_path / "outside"
    original_apply = CheckpointStore._apply_state
    swapped = {"done": False}

    def swap_parent(self, path, state, backup_dir, transaction_id,
                    expected_current=None):
        if not swapped["done"]:
            swapped["done"] = True
            parent.rename(detached)
            outside.mkdir()
            (outside / target.name).write_text("v3\n", encoding="utf-8")
            parent.symlink_to(outside, target_is_directory=True)
        return original_apply(
            self, path, state, backup_dir, transaction_id, expected_current,
        )

    monkeypatch.setattr(CheckpointStore, "_apply_state", swap_parent)
    result = rewind_to(
        "s-parent-swap", "u1",
        idempotency_key=plan["idempotency_key"],
        expected_plan_hash=plan["plan_hash"],
    )

    assert result["status"] != "committed"
    assert store.get_session("s-parent-swap")["head_id"] == assistants[-1]
    assert (outside / target.name).read_text(encoding="utf-8") == "v3\n"
    assert (detached / target.name).read_text(encoding="utf-8") == "v3\n"


def test_head_meta_write_failure_keeps_memory_and_files_at_source(
    store, tmp_path, monkeypatch,
):
    from openprogram.agent._rewind import rewind_to

    target = tmp_path / "work" / "same.py"
    assistants, _journal = _seed_three_turns(store, "s-meta-fail", target)
    git, _index = store._open("s-meta-fail")
    monkeypatch.setattr(
        git, "write_meta", lambda _meta: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = rewind_to("s-meta-fail", "u1", idempotency_key="meta-fail")

    assert result["status"] == "rolled_back"
    assert store.get_session("s-meta-fail")["head_id"] == assistants[-1]
    assert target.read_text(encoding="utf-8") == "v3\n"


def test_head_cas_reads_durable_head_across_store_instances(store, tmp_path):
    target = tmp_path / "work" / "same.py"
    assistants, _journal = _seed_three_turns(store, "s-durable-cas", target)
    other = SessionStore(store.root_path)
    other.set_head("s-durable-cas", "a2")

    assert not store.compare_and_set_head(
        "s-durable-cas", assistants[-1], "ROOT",
    )
    assert other.get_session("s-durable-cas")["head_id"] == "a2"


def test_mixed_interrupted_intent_is_automatically_rolled_back(store, tmp_path):
    from openprogram.agent._rewind import recover_session_rewinds

    first = tmp_path / "work" / "first.py"
    assistants, journal = _seed_three_turns(store, "s-mixed-recovery", first)
    second = tmp_path / "work" / "second.py"
    second.write_text("before\n", encoding="utf-8")
    journal.backup_before_edit(assistants[-1], str(second))
    second.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit(assistants[-1], str(second), operation="edit")
    turn_ids = list(reversed(assistants))
    plan = journal.plan_rewind_operation(turn_ids)
    first.write_text("v0\n", encoding="utf-8")
    key = "mixed-recovery"
    intent_path = journal._rewind_intent_path(key)
    manifest.save(intent_path, {
        "version": 1,
        "transaction_id": "rewind_partial",
        "idempotency_key": key,
        "turn_ids": turn_ids,
        "expected_head_id": assistants[-1],
        "target_head_id": "ROOT",
        "target_msg_id": "u1",
        "status": "applying",
        "actions": plan["actions"],
        "conflicts": [],
        "unavailable": [],
        "error": None,
    })

    results = recover_session_rewinds("s-mixed-recovery", store=store)

    assert results[0]["status"] == "rolled_back"
    assert first.read_text(encoding="utf-8") == "v3\n"
    assert second.read_text(encoding="utf-8") == "after\n"
    assert store.get_session("s-mixed-recovery")["head_id"] == assistants[-1]


def test_idempotent_replay_is_bound_to_original_target(store, tmp_path):
    from openprogram.agent._rewind import plan_rewind, rewind_to

    target = tmp_path / "work" / "same.py"
    _seed_three_turns(store, "s-idempotent", target)
    plan = plan_rewind("s-idempotent", "u2")
    first = rewind_to(
        "s-idempotent", "u2",
        idempotency_key=plan["idempotency_key"],
        expected_plan_hash=plan["plan_hash"],
    )
    replay = rewind_to(
        "s-idempotent", "u2",
        idempotency_key=plan["idempotency_key"],
        expected_plan_hash=plan["plan_hash"],
    )
    conflict = rewind_to(
        "s-idempotent", "u1",
        idempotency_key=plan["idempotency_key"],
        expected_plan_hash=plan["plan_hash"],
    )

    assert first["status"] == "committed"
    assert replay["status"] == "committed"
    assert replay["replayed"] is True
    assert replay["head_changed"] is False
    assert conflict["status"] == "idempotency_conflict"
    assert store.get_session("s-idempotent")["head_id"] == "a1"
    assert target.read_text(encoding="utf-8") == "v1\n"
