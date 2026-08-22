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
