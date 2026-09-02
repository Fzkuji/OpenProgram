"""Request correlation and durable idempotency for project-file WS actions."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import types
import uuid
from pathlib import Path

import pytest

from openprogram.store.project import project_store
from openprogram.webui import server
from openprogram.webui.ws_actions import files


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
    assert db_path.stat().st_mode & 0o777 == 0o600
    assert db_path.parent.stat().st_mode & 0o777 == 0o700


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


def test_large_mutation_witness_is_bounded(project, monkeypatch):
    large = project / "large.bin"
    large.write_bytes(b"x" * (files._IDENTITY_DIGEST_MAX_BYTES + 1))
    monkeypatch.setattr(files, "_file_digest", lambda _target: pytest.fail(
        "large-file witness must not read file content"
    ))
    identity = files._identity("p1", "large.bin")
    assert identity["size"] == files._IDENTITY_DIGEST_MAX_BYTES + 1
    assert "digest" not in identity


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
