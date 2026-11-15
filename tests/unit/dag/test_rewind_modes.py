"""Rewind's three modes, per-prompt checkpoints, atomic conflict refusal, Undo and Redo."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openprogram.runtime.conversation import rewind_marks
from openprogram.runtime.conversation.rewind import list_rewind_points, plan_rewind, rewind_to
from openprogram.state.store.session.rewind_recovery import recover_session_rewinds
from openprogram.state.store.session.session_store import SessionStore
from openprogram.state.store.snapshot.checkpoint import (
    CheckpointStore,
    PromptCheckpoints,
    gc_evict_old,
)
from openprogram.state.store.snapshot.checkpoint.paths import session_backup_root


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    value = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", value,
        raising=False,
    )
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    return value


def _append(store, sid, msg_id, role, pred, display=None):
    store.append_message(sid, {
        "id": msg_id, "role": role, "content": msg_id, "predecessor": pred,
        **({"caller": "ROOT"} if role == "user" and display != "root" else {}),
        **({"display": display} if display else {}),
    })


class Session:
    """A conversation whose turns edit files through the real checkpoint calls."""

    def __init__(self, store, sid, *, checkpoints=True):
        self.store, self.sid, self.head, self.turn = store, sid, "ROOT", 0
        store.create_session(sid, "main", title="t")
        _append(store, sid, "ROOT", "user", None, display="root")
        self.journal = CheckpointStore(store._session_dir(sid))
        self.prompts = PromptCheckpoints(store._session_dir(sid))
        self.checkpoints = checkpoints

    def turn_edits(self, *edits: tuple[Path, str]) -> tuple[str, str]:
        self.turn += 1
        user, reply = f"u{self.turn}", f"a{self.turn}"
        _append(self.store, self.sid, user, "user", self.head)
        if self.checkpoints:
            self.prompts.capture(user)
        for path, text in edits:
            if self.checkpoints:
                self.prompts.track(str(path))
            self.journal.backup_before_edit(reply, str(path))
            path.write_text(text, encoding="utf-8")
            self.journal.commit_after_edit(reply, str(path), operation="edit")
        _append(self.store, self.sid, reply, "assistant", user)
        self.store.set_head(self.sid, reply)
        self.head = reply
        return user, reply


def _apply(sid, msg, mode="code_and_conversation"):
    plan = plan_rewind(sid, msg, mode=mode)
    assert plan["status"] == "ready", plan
    return plan, rewind_to(
        sid, msg, mode=mode,
        idempotency_key=plan["idempotency_key"], expected_plan_hash=plan["plan_hash"],
    )


@pytest.fixture
def seeded(store, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    a = work / "a.py"
    a.write_text("one\n", encoding="utf-8")
    session = Session(store, "s")
    session.turn_edits((a, "two\nmore\n"))
    session.turn_edits((a, "three\n"))
    return session, a


def test_conversation_mode_moves_head_and_leaves_files(seeded, store):
    session, a = seeded
    plan, result = _apply("s", "u2", "conversation")
    assert result["status"] == "committed" and result["new_head_id"] == "a1"
    assert a.read_text() == "three\n"
    assert store.get_session("s")["head_id"] == "a1"
    assert plan["has_changes"] and result["restored_paths"] == []
    assert rewind_marks.take_rewind_note("s").strip() == rewind_marks.NOTES["conversation_only"]


def test_code_mode_restores_files_and_keeps_head(seeded, store):
    session, a = seeded
    _plan, result = _apply("s", "u2", "code")
    assert result["status"] == "committed" and result["head_changed"] is False
    assert a.read_text() == "two\nmore\n"
    assert store.get_session("s")["head_id"] == "a2"
    assert rewind_marks.list_rewind_marks("s") == []
    assert rewind_marks.take_rewind_note("s").strip() == rewind_marks.NOTES["code_only"]


def test_both_mode_restores_to_the_prompt_and_reports_line_counts(seeded, store):
    session, a = seeded
    plan = plan_rewind("s", "u1")
    row = plan["files"][0]
    assert (row["path"], row["action"], row["additions"], row["deletions"]) == (str(a), "restore", 1, 1)
    assert plan["summary"] == {"files": 1, "additions": 1, "deletions": 1, "skipped": 0}
    assert plan["checkpoint"] == "prompt"
    _, result = _apply("s", "u1")
    assert result["status"] == "committed" and result["new_head_id"] == "ROOT"
    assert a.read_text() == "one\n"


def test_plan_writes_nothing(seeded):
    session, a = seeded
    a.write_text("by hand\n")
    root = session_backup_root(session.journal.session_dir)
    before = sorted(str(p) for p in root.rglob("*"))
    plan_rewind("s", "u1")
    assert sorted(str(p) for p in root.rglob("*")) == before


def test_a_file_created_later_is_deleted(store, tmp_path):
    new = tmp_path / "new.py"
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits((new, "x\n"))
    plan = plan_rewind("s", "u1")
    assert [(r["path"], r["action"]) for r in plan["files"]] == [(str(new), "delete")]
    _apply("s", "u1")
    assert not new.exists()


def test_later_shell_change_blocks_rewind(seeded, store):
    session, a = seeded
    a.write_text("a shell command wrote this\n")
    result = rewind_to("s", "u2")
    assert result["status"] == "blocked"
    assert store.get_session("s")["head_id"] == "a2"
    assert a.read_text() == "a shell command wrote this\n"


def test_a_shell_change_between_prompts_is_part_of_the_next_checkpoint(store, tmp_path):
    a = tmp_path / "a.py"
    a.write_text("one\n")
    session = Session(store, "s")
    session.turn_edits((a, "two\n"))
    a.write_text("shell between prompts\n")
    session.turn_edits((a, "three\n"))
    _apply("s", "u2")
    assert a.read_text() == "shell between prompts\n"


def test_file_another_session_changed_later_blocks_the_whole_restore(seeded, store, tmp_path):
    session, a = seeded
    b = tmp_path / "work" / "b.py"
    b.write_text("b0\n")
    session.turn_edits((b, "b1\n"))
    other = CheckpointStore(store._session_dir("other"))
    other.backup_before_edit("t", str(a))
    a.write_text("other session\n")
    other.commit_after_edit("t", str(a), operation="edit")
    plan, result = plan_rewind("s", "u1"), rewind_to("s", "u1")
    assert [(r["path"], r["reason"]) for r in plan["skipped"]] == [(str(a), "other_session")]
    assert result["status"] == "blocked"
    assert [r["path"] for r in result["skipped"]] == [str(a)]
    assert a.read_text() == "other session\n" and b.read_text() == "b1\n"
    assert store.get_session("s")["head_id"] == "a3"
    assert rewind_marks.take_rewind_note("s") == ""


def test_unsafe_path_blocks_the_whole_restore(seeded, tmp_path):
    session, a = seeded
    b = tmp_path / "work" / "b.py"
    b.write_text("b0\n")
    session.turn_edits((b, "b1\n"))
    target = tmp_path / "elsewhere.py"
    target.write_text("elsewhere\n")
    a.unlink()
    a.symlink_to(target)
    plan, result = plan_rewind("s", "u1"), rewind_to("s", "u1")
    assert [(r["path"], r["reason"]) for r in plan["skipped"]] == [(str(a), "unsafe_path")]
    assert b.read_text() == "b1\n" and target.read_text() == "elsewhere\n"
    assert result["status"] == "blocked"


def test_a_file_that_cannot_be_captured_does_not_stop_the_edit(store, tmp_path, monkeypatch):
    from openprogram.state.store import _current_turn_id, _store
    from openprogram.state.store.snapshot.checkpoint.helpers import checkpoint_before_edit

    class Shim:
        session_id, store = "s", None

    session = Session(store, "s")
    session.prompts.capture("u0")
    shim = Shim()
    shim.store = store
    path = tmp_path / "a.py"
    path.write_text("x\n")

    def unreadable(self, source):
        raise OSError("disk trouble")
    monkeypatch.setattr(PromptCheckpoints, "store_blob", unreadable)
    tokens = _store.set(shim), _current_turn_id.set("turn")
    try:
        assert checkpoint_before_edit(str(path)) is True
    finally:
        _store.reset(tokens[0])
        _current_turn_id.reset(tokens[1])
    assert session.prompts.get("u0")["files"][str(path)] == {"kind": "unavailable", "reason": "unreadable"}


def test_a_file_with_no_backup_is_reported_as_skipped(store, tmp_path, monkeypatch):
    path = tmp_path / "a.py"
    path.write_text("x\n")
    session = Session(store, "s")
    monkeypatch.setattr(PromptCheckpoints, "store_blob", lambda self, source: (_ for _ in ()).throw(OSError("no")))
    session.turn_edits()
    session.prompts.track(str(path))
    session.journal.backup_before_edit("a1", str(path))
    path.write_text("changed\n")
    session.journal.commit_after_edit("a1", str(path), operation="edit")
    monkeypatch.undo()
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", store, raising=False,
    )
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    plan = plan_rewind("s", "u1")
    assert plan["status"] == "blocked"
    assert [(r["path"], r["reason"]) for r in plan["skipped"]] == [(str(path), "no_backup")]


def test_stale_plan_is_refused_and_writes_nothing(seeded):
    session, a = seeded
    plan = plan_rewind("s", "u1")
    a.write_text("changed after the dialog opened\n")
    result = rewind_to("s", "u1", idempotency_key="k", expected_plan_hash=plan["plan_hash"])
    assert result["status"] == "aborted" and result["error"] == "stale_plan"
    assert a.read_text() == "changed after the dialog opened\n"


def test_legacy_session_is_synthesized_from_the_journal_without_rewriting_it(store, tmp_path):
    a = tmp_path / "a.py"
    a.write_text("one\n")
    session = Session(store, "s", checkpoints=False)
    session.turn_edits((a, "two\n"))
    a.write_text("by hand\n")
    plan = plan_rewind("s", "u1")
    assert plan["checkpoint"] == "legacy" and plan["files"][0]["reason"] == "changed_since_edit"
    assert not (session_backup_root(session.journal.session_dir) / "checkpoints").exists()
    result = rewind_to("s", "u1")
    assert result["status"] == "blocked" and [r["path"] for r in result["skipped"]] == [str(a)]
    assert a.read_text() == "by hand\n"


def test_legacy_session_restores_a_file_that_still_matches_its_journal(store, tmp_path):
    a = tmp_path / "a.py"
    a.write_text("one\n")
    session = Session(store, "s", checkpoints=False)
    session.turn_edits((a, "two\n"))
    plan, result = _apply("s", "u1")
    assert plan["checkpoint"] == "legacy" and plan["files"][0]["action"] == "restore"
    assert result["status"] == "committed" and a.read_text() == "one\n"


def test_evicted_range_offers_conversation_only(store, tmp_path):
    a = tmp_path / "a.py"
    a.write_text("one\n")
    session = Session(store, "s", checkpoints=False)
    session.turn_edits((a, "two\n"))
    session.turn_edits((a, "three\n"))
    gc_evict_old(session.journal.session_dir, max_turns=1)
    plan = plan_rewind("s", "u1")
    assert plan["status"] == "unavailable" and plan["code_available"] is False
    assert plan["code_unavailable_reason"] == "evicted"
    rows = {row["msg_id"]: row for row in list_rewind_points("s", None)}
    assert rows["u1"]["code_available"] is False and rows["u2"]["code_available"] is True
    assert plan_rewind("s", "u1", mode="conversation")["status"] == "ready"
    assert rewind_to("s", "u1", mode="code_and_conversation")["status"] == "unavailable"


def test_gc_ignores_transaction_directories_and_keeps_legacy_until_over_the_cap(store, tmp_path):
    session = Session(store, "s", checkpoints=False)
    root = session_backup_root(session.journal.session_dir)
    for name in ("intents", "intent-locks", "checkpoints", "blobs", "t1", "t2"):
        (root / name).mkdir(parents=True)
    assert gc_evict_old(session.journal.session_dir, max_turns=2) == 0
    assert gc_evict_old(session.journal.session_dir, max_turns=1) == 1
    assert (root / "intents").is_dir() and (root / "blobs").is_dir()
    assert len([p for p in ("t1", "t2") if (root / p).exists()]) == 1


def test_gc_drops_old_checkpoints_and_only_their_unreferenced_blobs(store, tmp_path):
    a = tmp_path / "a.py"
    a.write_text("v0\n")
    session = Session(store, "s")
    session.turn_edits((a, "v1\n"))
    a.write_text("v1 bash\n")
    session.turn_edits((a, "v2\n"))
    a.write_text("v2 bash\n")
    session.prompts.capture("u3")
    gc_evict_old(session.journal.session_dir, max_checkpoints=2)
    assert session.prompts.ids() == {"u2", "u3"}
    kept = {entry["digest"].removeprefix("sha256:") for row in session.prompts.all() for entry in row["files"].values()}
    assert {path.name for path in session.prompts.blobs.iterdir()} == kept


def test_undo_and_redo_move_messages_only(seeded, store):
    session, a = seeded
    _, result = _apply("s", "u2")
    assert a.read_text() == "two\nmore\n"
    marks = rewind_marks.list_rewind_marks("s")
    assert [(m["variant"], m["anchor_head_id"]) for m in marks] == [("pending", "a1")]

    undone = rewind_marks.undo_rewind("s")
    assert undone["status"] == "committed" and undone["new_head_id"] == "a2"
    assert store.get_session("s")["head_id"] == "a2" and a.read_text() == "two\nmore\n"
    assert undone["marks"] == [] and undone["user_text"] == "u2"
    assert rewind_marks.take_rewind_note("s").strip() == rewind_marks.NOTES["undo_kept_files"]
    assert rewind_marks.redo_rewind("s")["code"] == "retired"


def _send_after_rewind(store, sid, user, reply, pred):
    rewind_marks.take_rewind_note(sid)
    _append(store, sid, user, "user", pred)
    _append(store, sid, reply, "assistant", user)
    store.set_head(sid, reply)


def test_a_sent_rewind_undoes_to_a_redo_mark(seeded, store):
    _apply("s", "u2")
    _send_after_rewind(store, "s", "u9", "a9", "a1")
    undone = rewind_marks.undo_rewind("s")
    assert undone["user_text"] == "" and [m["variant"] for m in undone["marks"]] == ["forward"]
    redone = rewind_marks.redo_rewind("s")
    assert redone["new_head_id"] == "a9" and [m["variant"] for m in redone["marks"]] == ["back"]


def test_a_mark_cannot_switch_while_a_rewind_is_unsent(seeded, store):
    _apply("s", "u2", mode="conversation")
    _send_after_rewind(store, "s", "u9", "a9", "a1")
    first = store.get_session("s")["rewind_edges"][0]["id"]
    _apply("s", "u9", mode="conversation")
    refused = rewind_marks.undo_rewind("s", edge_id=first)
    assert refused["status"] == "refused" and refused["code"] == "unsent"
    assert store.get_session("s")["head_id"] == "a1"


def test_an_archived_conversation_cannot_rewind_or_switch(seeded, store):
    _apply("s", "u2", mode="conversation")
    store.set_archived("s", True)
    plan = plan_rewind("s", "u1")
    assert plan["status"] == "blocked" and plan["error_code"] == "archived"
    assert rewind_to("s", "u1")["status"] == "blocked"
    assert rewind_marks.undo_rewind("s")["code"] == "archived"


def test_a_code_only_note_survives_a_mark_switch_until_the_next_send(seeded, store):
    _apply("s", "u2", mode="code")
    _apply("s", "u2", mode="conversation")
    _send_after_rewind(store, "s", "u9", "a9", "a1")
    plan = plan_rewind("s", "u1", mode="code")
    rewind_to("s", "u1", mode="code", idempotency_key=plan["idempotency_key"], expected_plan_hash=plan["plan_hash"])
    assert rewind_marks.undo_rewind("s")["status"] == "committed"
    assert rewind_marks.take_rewind_note("s").strip() == rewind_marks.NOTES["code_only"]


def test_undo_of_a_restoring_rewind_leaves_out_the_code_only_note(seeded, store):
    _apply("s", "u2")
    store.update_session("s", pending_code_note=True)
    rewind_marks.undo_rewind("s")
    assert rewind_marks.take_rewind_note("s").strip() == rewind_marks.NOTES["undo_kept_files"]


def test_conversation_only_with_only_skipped_files_warns(seeded, store):
    session, a = seeded
    other = CheckpointStore(store._session_dir("other"))
    other.backup_before_edit("t", str(a))
    a.write_text("other\n")
    other.commit_after_edit("t", str(a), operation="edit")
    plan = plan_rewind("s", "u2", mode="conversation")
    assert plan["has_changes"] is False and plan["skipped"]
    _apply("s", "u2", mode="conversation")
    assert rewind_marks.take_rewind_note("s").strip() == rewind_marks.NOTES["conversation_only"]


def test_a_rewind_with_nothing_to_say_keeps_an_unsent_warning(store, tmp_path):
    x = tmp_path / "x.py"
    x.write_text("x0\n")
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits()
    session.turn_edits((x, "x1\n"))
    _apply("s", "u3", mode="conversation")
    assert store.get_session("s")["pending_rewind_note"] == {"kind": "conversation_only"}
    _apply("s", "u1", mode="conversation")
    assert rewind_marks.take_rewind_note("s").strip() == rewind_marks.NOTES["conversation_only"]


def test_undoing_a_later_rewind_gives_back_the_earlier_warning(store, tmp_path):
    x = tmp_path / "x.py"
    x.write_text("x0\n")
    session = Session(store, "s")
    session.turn_edits()
    session.turn_edits()
    session.turn_edits((x, "x1\n"))
    _apply("s", "u3", mode="conversation")
    _apply("s", "u1", mode="conversation")
    rewind_marks.undo_rewind("s")
    assert store.get_session("s")["pending_rewind_note"] == {"kind": "conversation_only"}


def test_another_session_writing_before_our_later_edit_still_protects_the_file(store, tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("v0")
    session = Session(store, "s")
    session.turn_edits((a, "A1"))
    other = CheckpointStore(store._session_dir("other"))
    other.backup_before_edit("t", str(a))
    a.write_text("A1+B work")
    other.commit_after_edit("t", str(a), operation="edit")
    session.turn_edits((a, "A1+B work+A2"))
    plan, result = plan_rewind("s", "u1"), rewind_to("s", "u1")
    assert [(r["path"], r["reason"]) for r in plan["skipped"]] == [(str(a), "other_session")]
    assert result["status"] == "blocked" and a.read_text() == "A1+B work+A2"


def test_an_edit_another_session_has_started_protects_the_file(store, tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("v0")
    session = Session(store, "s")
    session.turn_edits((a, "A1"))
    other = CheckpointStore(store._session_dir("other"))
    other.backup_before_edit("t", str(a))
    assert [r["reason"] for r in plan_rewind("s", "u1")["skipped"]] == ["other_session"]
    other.abort_edit("t", str(a))
    assert plan_rewind("s", "u1")["skipped"] == []


def test_a_write_by_another_session_before_the_prompt_does_not_protect_the_file(store, tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("v0")
    other = CheckpointStore(store._session_dir("other"))
    other.backup_before_edit("t", str(a))
    a.write_text("B")
    other.commit_after_edit("t", str(a), operation="edit")
    session = Session(store, "s")
    session.turn_edits((a, "A1"))
    _, result = _apply("s", "u1")
    assert result["status"] == "committed" and a.read_text() == "B"


def _hidden(directory: Path) -> list[str]:
    return sorted(item.name for item in directory.iterdir() if item.name.startswith("."))


def test_a_committed_rewind_leaves_no_guard_files(seeded):
    _, a = seeded
    _, result = _apply("s", "u1")
    assert result["status"] == "committed" and _hidden(a.parent) == []


def test_a_guard_that_no_longer_holds_the_moved_file_is_kept(seeded, store):
    session, a = seeded
    plan = plan_rewind("s", "u1")
    original = CheckpointStore._discard_guards

    def tamper(self, intent, intent_path):
        for action in intent["actions"]:
            Path(action["guard_path"]).write_text("someone else")
        original(self, intent, intent_path)

    CheckpointStore._discard_guards = tamper
    try:
        rewind_to("s", "u1", idempotency_key=plan["idempotency_key"], expected_plan_hash=plan["plan_hash"])
    finally:
        CheckpointStore._discard_guards = original
    assert len(_hidden(a.parent)) == 1


def test_recovery_sweeps_guards_a_crash_left_after_commit(seeded, store):
    session, a = seeded
    plan = plan_rewind("s", "u1")
    original = CheckpointStore._discard_guards
    CheckpointStore._discard_guards = lambda *_args: None
    try:
        rewind_to("s", "u1", idempotency_key=plan["idempotency_key"], expected_plan_hash=plan["plan_hash"])
    finally:
        CheckpointStore._discard_guards = original
    assert len(_hidden(a.parent)) == 1
    recover_session_rewinds("s", store=store)
    assert _hidden(a.parent) == []


class _Crash(BaseException):
    """Stands in for the process dying: nothing after it runs."""


def _crash_at_link(monkeypatch, *, after: int = 0):
    real = os.link
    calls = {"count": 0}

    def link(*args, **kwargs):
        if calls["count"] >= after:
            raise _Crash()
        calls["count"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    return lambda: monkeypatch.setattr(os, "link", real)


def _hang_on_crash(sid, msg, mode):
    plan = plan_rewind(sid, msg, mode=mode)
    with pytest.raises(_Crash):
        rewind_to(sid, msg, mode=mode, idempotency_key=plan["idempotency_key"], expected_plan_hash=plan["plan_hash"])
    return plan


def test_recovery_puts_back_a_file_moved_aside_by_a_crash(seeded, store, monkeypatch):
    session, a = seeded
    restore = _crash_at_link(monkeypatch)
    plan = _hang_on_crash("s", "u1", "code_and_conversation")
    restore()
    assert not a.exists()
    results = recover_session_rewinds("s", store=store)
    assert results[0]["status"] == "rolled_back"
    assert a.read_text() == "three\n" and _hidden(a.parent) == []
    assert store.get_session("s")["head_id"] == plan["source_head_id"]


def test_recovery_rolls_back_every_file_of_a_half_applied_rewind(store, tmp_path, monkeypatch):
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("a0"), second.write_text("b0")
    session = Session(store, "s")
    session.turn_edits((first, "a1"), (second, "b1"))
    restore = _crash_at_link(monkeypatch, after=1)
    _hang_on_crash("s", "u1", "code_and_conversation")
    restore()
    assert recover_session_rewinds("s", store=store)[0]["status"] == "rolled_back"
    assert first.read_text() == "a1" and second.read_text() == "b1"
    assert not [name for name in _hidden(tmp_path) if name.endswith((".guard", ".tmp"))]


def test_rolling_back_a_code_only_rewind_leaves_no_note(seeded, store, monkeypatch):
    session, a = seeded
    restore = _crash_at_link(monkeypatch)
    _hang_on_crash("s", "u1", "code")
    restore()
    results = recover_session_rewinds("s", store=store)
    assert results[0]["status"] == "rolled_back" and a.read_text() == "three\n"
    assert not store.get_session("s").get("pending_code_note")
    assert rewind_marks.take_rewind_note("s") == ""


def test_undo_refuses_when_head_moved(seeded, store):
    _apply("s", "u2")
    refused = rewind_marks.undo_rewind("s", expected_head_id="other")
    assert refused["status"] == "refused" and refused["code"] == "stale"
    assert store.get_session("s")["head_id"] == "a1"


def test_sending_turns_the_pending_mark_into_a_lasting_one(seeded, store):
    _apply("s", "u2")
    rewind_marks.take_rewind_note("s")
    assert rewind_marks.take_rewind_note("s") == ""
    _append(store, "s", "u9", "user", "a1")
    _append(store, "s", "a9", "assistant", "u9")
    store.set_head("s", "a9")
    assert [m["variant"] for m in rewind_marks.list_rewind_marks("s")] == ["back"]
    assert rewind_marks.undo_rewind("s")["new_head_id"] == "a2"
    assert rewind_marks.redo_rewind("s")["new_head_id"] == "a9"


def test_rewind_drops_read_baselines(seeded, tmp_path):
    from openprogram.state.store.snapshot import read_tracking

    session, a = seeded
    read_tracking.mark_seen(str(a), session_id="s")
    _apply("s", "u2")
    assert read_tracking.check_fresh(str(a), session_id="s") == read_tracking.NEVER_READ


def test_rewind_blocks_a_change_no_journal_saw_before_cas(seeded, store, monkeypatch):
    session, a = seeded
    a.write_text("hand written\n")
    original = CheckpointStore._apply_state
    calls = {"count": 0}

    def fail_after_write(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        calls["count"] += 1
        return value
    monkeypatch.setattr(CheckpointStore, "_apply_state", fail_after_write)
    monkeypatch.setattr(store, "compare_and_set_head", lambda *a, **k: False)
    plan = plan_rewind("s", "u1", recover=False)
    result = rewind_to("s", "u1", idempotency_key="k", expected_plan_hash=plan["plan_hash"])
    assert result["status"] == "blocked" and calls["count"] == 0
    assert a.read_text() == "hand written\n"


def test_branch_session_carries_checkpoints(seeded, store):
    from openprogram.runtime.context.branch_session import branch_session

    session, a = seeded
    new_id = branch_session("s", "a2", store=store)
    copy_user = next(
        row for row in list_rewind_points(new_id, None) if row["user_text"] == "u1"
    )
    assert copy_user["code_available"] is True and copy_user["checkpoint"] == "prompt"
    plan = plan_rewind(new_id, copy_user["msg_id"])
    assert plan["files"] and plan["files"][0]["action"] == "restore"
    _apply(new_id, copy_user["msg_id"])
    assert a.read_text() == "one\n"


def test_branch_of_a_legacy_session_says_code_cannot_be_restored(store, tmp_path):
    from openprogram.runtime.context.branch_session import branch_session

    a = tmp_path / "a.py"
    a.write_text("one\n")
    session = Session(store, "s", checkpoints=False)
    session.turn_edits((a, "two\n"))
    new_id = branch_session("s", "a1", store=store)
    row = list_rewind_points(new_id, None)[0]
    assert row["code_available"] is False and row["code_unavailable_reason"] == "no_checkpoint"
    assert json.loads(json.dumps(row))


def test_points_have_no_limit_when_asked_and_hide_runtime_prompts(store):
    session = Session(store, "s")
    for _ in range(12):
        session.turn_edits()
    _append(store, "s", "note", "user", session.head, display="runtime")
    store.set_head("s", "note")
    points = list_rewind_points("s", None)
    assert len(points) == 12 and points[0]["msg_id"] == "u12"


def test_points_before_the_first_reply_are_only_the_first_prompt(store):
    session = Session(store, "s")
    _append(store, "s", "u1", "user", "ROOT")
    _append(store, "s", "steer", "user", "u1")
    _append(store, "s", "a1", "assistant", "steer")
    _append(store, "s", "u2", "user", "a1")
    _append(store, "s", "a2", "assistant", "u2")
    store.set_head("s", "a2")
    assert [p["msg_id"] for p in list_rewind_points("s", None)] == ["u2", "u1"]
