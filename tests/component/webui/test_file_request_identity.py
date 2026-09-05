"""Request correlation and durable idempotency for project-file WS actions."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import threading
import time
import types
import uuid
from pathlib import Path

import pytest

from openprogram.store.project import project_store
from openprogram.webui import server
from openprogram.webui.ws_actions import files
from openprogram.webui.ws_actions import turn_files


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, value):
        self.sent.append(json.loads(value))


def run(handler, command):
    ws = FakeWS()
    asyncio.run(handler(ws, command))
    return ws.sent[0]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "source.txt").write_text("before", encoding="utf-8")
    monkeypatch.setattr(
        project_store, "get_project",
        lambda project_id: types.SimpleNamespace(id="p1", path=str(root))
        if project_id == "p1" else None,
    )
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    return root


def test_file_result_echoes_request_id_on_success_and_stale(project):
    request_id = str(uuid.uuid4())
    frame = run(files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "page_size": 1,
        "request_id": request_id,
    })
    assert frame["data"]["request_id"] == request_id
    stale = run(files.handle_project_file_tree, {
        "project_id": "p1", "path": "", "cursor": "missing",
        "request_id": request_id,
    })
    assert stale["data"]["request_id"] == request_id
    assert stale["data"]["error_code"] == "STALE_SNAPSHOT"


def test_dispatch_requires_uuid_and_idempotency_key():
    from openprogram.webui.ws_errors import OperationError

    with pytest.raises(OperationError):
        server._validate_file_request({"request_id": "request-1"}, "project_file_read")
    with pytest.raises(OperationError):
        server._validate_file_request({"request_id": str(uuid.uuid4())}, "project_file_write")
    with pytest.raises(OperationError):
        server._validate_file_request({
            "request_id": str(uuid.uuid4()), "idempotency_key": "human-key",
        }, "project_file_write")


def test_file_write_replay_collision_and_restart(project):
    from openprogram.store.file_operations import default_file_operation_store

    request_id = str(uuid.uuid4())
    command = {
        "project_id": "p1", "path": "source.txt", "content": "after",
        "expected_mtime": (project / "source.txt").stat().st_mtime,
        "idempotency_key": "write-once",
        "request_id": request_id,
    }
    first = run(files.handle_project_file_write, command)["data"]
    assert first["ok"] is True
    mtime = (project / "source.txt").stat().st_mtime
    store = default_file_operation_store()
    payload = json.loads(store.get(first["operation_id"])["payload_json"])
    assert "content" not in payload
    assert payload["content_sha256"] == files._canonical_mutation_payload({"content": "after"})["content_sha256"]
    assert payload["content_byte_length"] == len("after".encode("utf-8"))

    replay = run(files.handle_project_file_write, command)["data"]
    assert replay["operation_id"] == first["operation_id"]
    assert (project / "source.txt").stat().st_mtime == mtime

    collision = run(files.handle_project_file_write, {
        **command, "path": "other.txt",
    })["data"]
    assert collision["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"
    content_collision = run(files.handle_project_file_write, {
        **command, "content": "different",
    })["data"]
    assert content_collision["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"

    # A fresh store handle observes the same durable completed record.
    from openprogram.store.file_operations import fingerprint
    row, owner = store.begin(
        "p1", "project_file_write", "write-once",
        fingerprint(files._canonical_mutation_payload({
            "path": "source.txt", "content": "after",
            "expected_mtime": command["expected_mtime"],
        })),
    )
    assert not owner and store.replay(row)["operation_id"] == first["operation_id"]


def test_file_write_baseline_revision_is_part_of_durable_identity(project):
    read = run(files.handle_project_file_read, {
        "project_id": "p1", "path": "source.txt",
    })["data"]
    command = {
        "project_id": "p1", "path": "source.txt", "content": "after-revision",
        "expected_mtime": read["mtime"], "baseline_revision": read["revision"],
        "idempotency_key": "write-revision", "request_id": str(uuid.uuid4()),
    }
    first = run(files.handle_project_file_write, command)["data"]
    assert first["status"] == "ready"
    collision = run(files.handle_project_file_write, {
        **command, "baseline_revision": "0" * 64,
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert collision["status"] == "conflict"
    assert collision["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_copy_replay_and_terminal_recovery_are_durable(project, tmp_path):
    request_id = str(uuid.uuid4())
    command = {
        "project_id": "p1", "path": "source.txt", "new_path": "copy.txt",
        "idempotency_key": "copy-once", "request_id": request_id,
    }
    first = run(files.handle_project_file_copy, command)["data"]
    assert first["status"] == "ready"
    replay = run(files.handle_project_file_copy, command)["data"]
    assert replay["operation_id"] == first["operation_id"]
    assert (project / "copy.txt").read_text(encoding="utf-8") == "before"

    from openprogram.store.file_operations import FileOperationStore
    db_path = tmp_path / "empty-profile" / "file_operations.db"
    store = FileOperationStore(db_path)
    row, owner = store.begin("p1", "project_file_delete", "crash-key", "fp",
                              payload={"path": "missing.txt"},
                              before={"source": {"exists": True}},
                              after={"target": {"exists": False}})
    assert owner
    store.finish(row["operation_id"], {
        "status": "recovery_required",
        "error_code": "RECOVERY_REQUIRED",
        "error": "file operation state cannot be reconciled safely",
    }, status="recovery_required", phase="recovery_required")
    reopened = FileOperationStore(db_path)
    replayed = reopened.replay(reopened.begin(
        "p1", "project_file_delete", "crash-key", "fp",
    )[0])
    assert replayed["status"] == "recovery_required"
    assert "in_flight" not in replayed
    from openprogram._compat import user_private_metadata
    assert user_private_metadata(db_path.stat(), exact_mode=0o600)
    assert user_private_metadata(db_path.parent.stat(), exact_mode=0o700)


def test_inflight_after_image_is_not_assumed_to_be_our_write(project):
    from openprogram.store.file_operations import default_file_operation_store, fingerprint

    payload = {"path": "source.txt", "content": "same", "expected_mtime": None}
    canonical = files._canonical_mutation_payload(payload)
    before, after = files._mutation_states("p1", "project_file_write", canonical)
    store = default_file_operation_store()
    row, owner = store.begin(
        "p1", "project_file_write", "crashed-write", fingerprint(canonical),
        payload=canonical, before=before, after=after,
    )
    assert owner
    # An unrelated writer produces the same bytes after the intent was
    # persisted.  There is no durable apply token, so retry must stop.
    (project / "source.txt").write_text("same", encoding="utf-8")
    result = run(files.handle_project_file_write, {
        "project_id": "p1", **payload,
        "idempotency_key": "crashed-write", "request_id": str(uuid.uuid4()),
    })["data"]
    assert result["status"] == "recovery_required"
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert result["operation_id"] == row["operation_id"]


def test_inflight_before_image_is_retried_under_the_lock(project):
    from openprogram.store.file_operations import default_file_operation_store, fingerprint

    payload = {"path": "source.txt", "content": "retry", "expected_mtime": None}
    canonical = files._canonical_mutation_payload(payload)
    before, after = files._mutation_states("p1", "project_file_write", canonical)
    store = default_file_operation_store()
    row, owner = store.begin(
        "p1", "project_file_write", "retry-write", fingerprint(canonical),
        payload=canonical, before=before, after=after,
    )
    assert owner
    result = run(files.handle_project_file_write, {
        "project_id": "p1", **payload,
        "idempotency_key": "retry-write", "request_id": str(uuid.uuid4()),
    })["data"]
    assert result["status"] == "ready"
    assert result["operation_id"] == row["operation_id"]
    assert (project / "source.txt").read_text(encoding="utf-8") == "retry"


def test_operation_status_reconciles_inflight_to_terminal(project):
    from openprogram.store.file_operations import default_file_operation_store, fingerprint

    store = default_file_operation_store()
    row, owner = store.begin(
        "p1", "project_file_write", "status-key", fingerprint({"path": "source.txt"}),
        payload={"path": "source.txt"},
    )
    assert owner
    request_id = str(uuid.uuid4())
    pending = run(files.handle_project_file_operation_status, {
        "project_id": "p1", "operation_action": "project_file_write",
        "idempotency_key": "status-key", "operation_id": row["operation_id"],
        "request_id": request_id,
    })["data"]
    assert pending["status"] == "in_progress"
    assert pending["operation_id"] == row["operation_id"]
    store.finish(row["operation_id"], {"status": "ready"})
    terminal = run(files.handle_project_file_operation_status, {
        "project_id": "p1", "operation_action": "project_file_write",
        "idempotency_key": "status-key", "operation_id": row["operation_id"],
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert terminal["status"] == "ready"


def test_turn_operation_status_requires_its_own_identity():
    frame = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a",
        "msg_id": "turn-1",
        "operation_action": "revert_turn",
        "idempotency_key": "turn-key",
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert frame["action"] == "turn_operation_status"
    assert frame["status"] == "error"
    assert frame["error_code"] == "RECEIPT_UNAVAILABLE"


def test_turn_operation_status_reads_history_intent_by_turn_and_key(tmp_path, monkeypatch):
    from openprogram.store.session import session_store
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    session_dir = tmp_path / "session"
    journal = CheckpointStore(session_dir)
    target = tmp_path / "history.txt"
    target.write_text("before", encoding="utf-8")
    journal.backup_before_edit("turn-1", str(target))
    target.write_text("after", encoding="utf-8")
    journal.commit_after_edit("turn-1", str(target), operation="edit")
    ordinary = journal.apply_history_operation(
        "turn-1", "revert", idempotency_key="turn-key",
    )
    assert ordinary["status"] == "committed"

    closure_key = "turn-closure:revert:closure-key"
    closure = journal.apply_rewind_operation(
        [], expected_head_id=None, target_head_id=None,
        get_head=lambda: None,
        compare_and_set_head=lambda expected, target: expected is None and target is None,
        idempotency_key=closure_key, target_msg_id="revert:turn-2",
    )
    assert closure["status"] == "committed"

    class FakeStore:
        def _session_dir(self, session_id):
            assert session_id == "session-a"
            return session_dir

    monkeypatch.setattr(session_store, "default_store", lambda: FakeStore())
    terminal = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "turn-1",
        "operation_action": "revert_turn", "idempotency_key": "turn-key",
        "operation_id": ordinary["transaction_id"], "request_id": str(uuid.uuid4()),
    })["data"]
    assert terminal["status"] == "ready"
    assert terminal["operation_id"] == ordinary["transaction_id"]

    mismatch = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "turn-1",
        "operation_action": "revert_turn", "idempotency_key": "turn-key",
        "operation_id": "other-op", "request_id": str(uuid.uuid4()),
    })["data"]
    assert mismatch["status"] == "recovery_required"
    assert mismatch["error_code"] == "OPERATION_ID_MISMATCH"

    unknown = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "other-turn",
        "operation_action": "revert_turn", "idempotency_key": "turn-key",
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert unknown["status"] == "error"
    assert unknown["error_code"] == "RECEIPT_UNAVAILABLE"

    unsafe = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "../turn-1",
        "operation_action": "revert_turn", "idempotency_key": "turn-key",
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert unsafe["status"] == "error"
    assert unsafe["error_code"] == "INVALID_REQUEST"

    control = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "turn-1\x00",
        "operation_action": "revert_turn", "idempotency_key": "turn-key",
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert control["status"] == "error"
    assert control["error_code"] == "INVALID_REQUEST"

    global_terminal = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "turn-2",
        "operation_action": "revert_turn", "idempotency_key": "closure-key",
        "operation_id": closure["transaction_id"], "request_id": str(uuid.uuid4()),
    })["data"]
    assert global_terminal["status"] == "ready"
    assert global_terminal["operation_id"] == closure["transaction_id"]


def test_history_intent_crash_is_terminalized_on_restart(tmp_path, monkeypatch):
    from openprogram.store.session import session_store
    from openprogram.store.snapshot.checkpoint import manifest
    from openprogram.store.snapshot.checkpoint import store as checkpoint_store
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    session_dir = tmp_path / "session"
    journal = CheckpointStore(session_dir)
    target = tmp_path / "history.txt"
    target.write_text("before", encoding="utf-8")
    journal.backup_before_edit("turn-1", str(target))
    target.write_text("after", encoding="utf-8")
    journal.commit_after_edit("turn-1", str(target), operation="edit")
    original_save = manifest.save
    saves = 0

    def crash_after_applying(path, value):
        nonlocal saves
        saves += 1
        original_save(path, value)
        if saves == 2:
            raise RuntimeError("simulated restart")

    monkeypatch.setattr(checkpoint_store.manifest, "save", crash_after_applying)
    with pytest.raises(RuntimeError, match="simulated restart"):
        journal.apply_history_operation("turn-1", "revert", idempotency_key="crash-key")

    monkeypatch.setattr(checkpoint_store.manifest, "save", original_save)
    recovered = journal.recover_history_intents()
    assert len(recovered) == 1
    assert recovered[0]["status"] == "recovery_required"
    recovered_intent = journal.read_history_intent("turn-1", "revert", "crash-key")
    assert recovered_intent["status"] == "recovery_required"
    assert recovered_intent["error_code"] == "RECOVERY_REQUIRED"
    legacy_intent = dict(recovered_intent)
    legacy_intent.pop("error_code")
    manifest.save(journal._intent_path("turn-1", "revert", "crash-key"), legacy_intent)
    recovered_intent = journal.read_history_intent("turn-1", "revert", "crash-key")
    assert recovered_intent["error_code"] == "RECOVERY_REQUIRED"

    class FakeStore:
        def _session_dir(self, session_id):
            assert session_id == "session-a"
            return session_dir

    monkeypatch.setattr(session_store, "default_store", lambda: FakeStore())
    status = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "turn-1",
        "operation_action": "revert_turn", "idempotency_key": "crash-key",
        "operation_id": recovered_intent["transaction_id"],
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert status["status"] == "recovery_required"
    assert status["error_code"] == "RECOVERY_REQUIRED"


def test_rewind_recovery_persists_error_code_for_status(tmp_path, monkeypatch):
    from openprogram.store.session import session_store
    from openprogram.store.snapshot.checkpoint import manifest
    from openprogram.store.snapshot.checkpoint import store as checkpoint_store
    from openprogram.store.snapshot.checkpoint import CheckpointStore

    session_dir = tmp_path / "session"
    journal = CheckpointStore(session_dir)
    original_save = manifest.save
    saves = 0

    def crash_after_applying(path, value):
        nonlocal saves
        saves += 1
        original_save(path, value)
        if saves == 2:
            raise RuntimeError("simulated rewind restart")

    monkeypatch.setattr(checkpoint_store.manifest, "save", crash_after_applying)
    stored_key = "turn-closure:revert:rewind-key"
    with pytest.raises(RuntimeError, match="simulated rewind restart"):
        journal.apply_rewind_operation(
            [], expected_head_id=None, target_head_id=None,
            get_head=lambda: None,
            compare_and_set_head=lambda expected, target: True,
            idempotency_key=stored_key, target_msg_id="revert:turn-r",
        )

    monkeypatch.setattr(checkpoint_store.manifest, "save", original_save)
    recovered = journal.recover_rewind_intents(
        get_head=lambda: "external",
        compare_and_set_head=lambda intent, expected, target: False,
    )
    assert recovered[0]["status"] == "recovery_required"
    assert recovered[0]["error_code"] == "RECOVERY_REQUIRED"
    rewind_path = journal._rewind_intent_path(stored_key)
    legacy_intent = dict(json.loads(rewind_path.read_text(encoding="utf-8")))
    legacy_intent.pop("error_code")
    manifest.save(rewind_path, legacy_intent)
    normalized = journal.read_rewind_intent(stored_key)
    assert normalized["error_code"] == "RECOVERY_REQUIRED"

    class FakeStore:
        def _session_dir(self, session_id):
            assert session_id == "session-a"
            return session_dir

    monkeypatch.setattr(session_store, "default_store", lambda: FakeStore())
    status = run(turn_files.handle_turn_operation_status, {
        "session_id": "session-a", "msg_id": "turn-r",
        "operation_action": "revert_turn", "idempotency_key": "rewind-key",
        "operation_id": recovered[0]["transaction_id"],
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert status["status"] == "recovery_required"
    assert status["error_code"] == "RECOVERY_REQUIRED"


def test_file_operation_compaction_has_explicit_safe_retention(tmp_path):
    from openprogram.store.file_operations import (
        FILE_OPERATION_MAX_TERMINAL_RECORDS,
        FILE_OPERATION_TERMINAL_TTL_SECONDS,
        FileOperationStore,
        fingerprint,
    )

    db_path = tmp_path / "state" / "file_operations.db"
    store = FileOperationStore(db_path)
    rows = []
    for index, status in enumerate(("completed", "conflict", "error", "in_flight", "recovery_required")):
        row, owner = store.begin("p", "action", f"key-{index}", fingerprint({"index": index}))
        assert owner
        if status != "in_flight":
            store.finish(row["operation_id"], {"status": status}, status=status, phase=status)
        rows.append(row["operation_id"])
    old = time.time() - FILE_OPERATION_TERMINAL_TTL_SECONDS - 1
    with store._connect() as db:
        db.execute("UPDATE file_operations SET updated_at=? WHERE status IN ('completed','conflict','error')", (old,))
    assert store.compact(now=time.time()) == 3
    assert store.get(rows[3]) is not None
    assert store.get(rows[4]) is not None

    # The max-entry policy is explicit and does not count protected states.
    for index in range(FILE_OPERATION_MAX_TERMINAL_RECORDS + 1):
        row, owner = store.begin("p", "max", f"key-{index}", fingerprint({"max": index}))
        assert owner
        store.finish(row["operation_id"], {"status": "completed"})
    assert store.compact(now=time.time()) >= 1
    assert store.get(rows[3]) is not None
    assert store.get(rows[4]) is not None


def test_large_mutation_witness_is_bounded(project, monkeypatch):
    large = project / "large.bin"
    large.write_bytes(b"x" * (files._IDENTITY_DIGEST_MAX_BYTES + 1))
    monkeypatch.setattr(files, "_file_digest", lambda _target: pytest.fail(
        "large-file witness must not read file content"
    ))
    identity = files._identity("p1", "large.bin")
    assert identity["size"] == files._IDENTITY_DIGEST_MAX_BYTES + 1
    assert "digest" not in identity


def test_file_digest_stops_at_bound_when_file_grows_during_read(project, monkeypatch):
    target = project / "growing-digest.txt"
    target.write_bytes(b"a" * 8192)
    original_open = open

    class GrowingReader:
        def __init__(self, stream):
            self.stream = stream
            self.did_grow = False

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            data = self.stream.read(size)
            if not self.did_grow:
                self.did_grow = True
                target.write_bytes(b"a" * (files._READ_DIGEST_MAX_BYTES + 1))
            return data

    def growing_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return GrowingReader(stream) if os.fspath(path) == os.fspath(target) else stream

    monkeypatch.setattr(files, "open", growing_open, raising=False)
    assert files._file_digest(str(target)) is None


def test_owner_process_start_identity_rejects_pid_reuse(project):
    del project
    from openprogram.store.file_operations import current_owner_identity

    instance_id, pid, process_start = current_owner_identity()
    if process_start is None:
        pytest.skip("process start identity is unavailable on this platform")
    assert files._owner_process_alive({
        "owner_instance_id": instance_id,
        "owner_pid": pid,
        "owner_process_start": process_start,
    }) is False, "same-process stale rows must be recoverable when not active"
    assert files._owner_process_alive({
        "owner_instance_id": "other-worker",
        "owner_pid": pid,
        "owner_process_start": "proc:pid-reused",
    }) is False
    assert files._owner_process_alive({
        "owner_instance_id": "other-worker",
        "owner_pid": pid,
        "owner_process_start": process_start,
    }) is True


def test_idempotency_fingerprint_normalizes_alias_paths(project):
    key = str(uuid.uuid4())
    mtime = (project / "source.txt").stat().st_mtime
    first = run(files.handle_project_file_write, {
        "project_id": "p1", "path": "./source.txt", "content": "alias",
        "expected_mtime": mtime, "idempotency_key": key,
        "request_id": str(uuid.uuid4()),
    })["data"]
    replay = run(files.handle_project_file_write, {
        "project_id": "p1", "path": "source.txt", "content": "alias",
        "expected_mtime": mtime, "idempotency_key": key,
        "request_id": str(uuid.uuid4()),
    })["data"]
    assert replay["operation_id"] == first["operation_id"]


def test_active_same_key_retry_returns_in_progress_without_waiting(project):
    from openprogram.webui.ws_actions.files import _durable_file_action

    started = threading.Event()
    release = threading.Event()
    key = str(uuid.uuid4())
    payload = {"path": "source.txt", "content": "held", "expected_mtime": None}

    def held():
        started.set()
        release.wait(timeout=5)
        return {"ok": True}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_durable_file_action, "p1", "project_file_write",
                             key, payload, held)
        assert started.wait(timeout=2)
        retry = _durable_file_action("p1", "project_file_write", key, payload,
                                     lambda: {"ok": True})
        assert retry["status"] == "in_progress"
        release.set()
        assert future.result()["status"] == "ready"


def test_review_stale_states_keep_their_protocol_code():
    assert files._normalise_file_result({"error": "path escapes project root"})["error_code"] == "INVALID_REQUEST"
    from openprogram.webui.ws_actions import turn_files

    for code in ("STALE_SNAPSHOT", "STALE_CURSOR"):
        result = turn_files._stable_file_result({"status": "stale", "error": code})
        assert result["status"] == "stale"
        assert result["error_code"] == code
