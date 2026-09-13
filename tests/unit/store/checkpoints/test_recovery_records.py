"""Malformed persisted rewinds cannot prevent later valid recovery."""
import json
from pathlib import Path

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.store.snapshot.checkpoint.paths import session_backup_root


def prepared():
    return {"status": "prepared", "actions": [], "expected_head_id": "old",
            "target_head_id": "new", "transaction_id": "transaction"}


@pytest.mark.parametrize("raw", [
    b"[]", b"null", b'"text"', b"{", b"\xff",
    json.dumps({**prepared(), "actions": None}).encode(),
    json.dumps({**prepared(), "actions": [None]}).encode(),
    json.dumps({**prepared(), "actions": [{"path": "/a"}]}).encode(),
    json.dumps({**prepared(), "expected_head_id": []}).encode(),
    json.dumps({"status": "prepared", "actions": []}).encode(),
    json.dumps({**prepared(), "status": []}).encode(),
])
def test_recovery_reports_bad_record_and_recovers_later_record(tmp_path, monkeypatch, raw):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    session = root / "sessions" / "session"
    directory = session_backup_root(session) / "intents"
    directory.mkdir(parents=True)
    bad, valid = directory / "a.json", directory / "b.json"
    bad.write_bytes(raw)
    valid.write_text(json.dumps(prepared()))
    heads = []

    def get_head():
        heads.append("read")
        return "old"

    def no_cas(*_):
        pytest.fail("neither malformed nor already-source HEAD-only record needs CAS")

    results = CheckpointStore(session).recover_rewind_intents(
        get_head=get_head, compare_and_set_head=no_cas,
    )
    assert results[0]["status"] == "recovery_required"
    assert results[0]["error_code"] == "RECOVERY_REQUIRED"
    assert results[0]["intent_path"] == str(bad)
    assert results[0]["restored_paths"] == []
    assert results[1]["status"] == "rolled_back"
    assert heads == ["read"]
    assert bad.read_bytes() == raw
    assert json.loads(valid.read_text())["status"] == "rolled_back"


@pytest.mark.parametrize("read_number", [2, 3], ids=["before-lock", "under-lock"])
def test_recovery_revalidates_record_after_scan(tmp_path, monkeypatch, read_number):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    session = root / "sessions" / "session"
    directory = session_backup_root(session) / "intents"
    directory.mkdir(parents=True)
    path = directory / "record.json"
    path.write_text(json.dumps(prepared()))
    original = Path.read_text
    count = 0

    def changed_read(self, *args, **kwargs):
        nonlocal count
        if self == path:
            count += 1
            if count == read_number:
                path.write_text("[]")
        return original(self, *args, **kwargs)

    def no_head(*_):
        pytest.fail("invalid re-read must not inspect or change HEAD")

    monkeypatch.setattr(Path, "read_text", changed_read)
    result = CheckpointStore(session).recover_rewind_intents(
        get_head=no_head, compare_and_set_head=no_head,
    )
    assert result[0]["status"] == "recovery_required"
    assert result[0]["intent_path"] == str(path)
    assert path.read_bytes() == b"[]"


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_recovery_does_not_swallow_operational_failure(tmp_path, monkeypatch, failure):
    session = tmp_path.resolve() / "sessions" / "session"
    directory = session_backup_root(session) / "intents"
    directory.mkdir(parents=True)
    path = directory / "record.json"
    path.write_text(json.dumps(prepared()))
    original = Path.read_text

    def failed_read(self, *args, **kwargs):
        if self == path:
            raise failure("read interrupted")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failed_read)
    with pytest.raises(failure, match="read interrupted"):
        CheckpointStore(session).recover_rewind_intents(
            get_head=lambda: pytest.fail("unexpected HEAD access"),
            compare_and_set_head=lambda *_: pytest.fail("unexpected HEAD update"),
        )


@pytest.mark.parametrize("owner, field, value", [
    ("rollback", "mode", {}),
    ("rollback", "mode", "invalid"),
    ("rollback", "blob_path", {}),
    ("rollback", "blob_path", "relative"),
    ("rollback", "blob_ref", []),
    ("rollback", "blob_ref", "../outside"),
    ("target", "parent_chain", []),
    ("target", "parent_chain", {"root": "/"}),
    ("record", "transaction_id", {}),
    ("record", "transaction_id", "../outside"),
])
def test_bad_execution_fields_do_not_execute_or_rewrite_record(
    tmp_path, monkeypatch, owner, field, value,
):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    session = root / "sessions" / "session"
    journal = CheckpointStore(session)
    target = root / "target.txt"
    target.write_text("before")
    journal.backup_before_edit("turn", str(target))
    target.write_text("after")
    journal.commit_after_edit("turn", str(target))
    plan = journal.plan_rewind_operation(["turn"])
    record = {**prepared(), "actions": plan["actions"]}
    if owner == "record":
        record[field] = value
    else:
        record["actions"][0][owner][field] = value
    target.write_text("before")
    directory = session_backup_root(session) / "intents"
    directory.mkdir()
    bad, valid = directory / "a.json", directory / "b.json"
    raw = json.dumps(record).encode()
    bad.write_bytes(raw)
    valid.write_text(json.dumps(prepared()))
    heads = []

    def get_head():
        heads.append("read")
        return "old"

    results = journal.recover_rewind_intents(
        get_head=get_head, compare_and_set_head=lambda *_: pytest.fail("unexpected HEAD update"),
    )
    assert results[0]["status"] == "recovery_required"
    assert results[0]["intent_path"] == str(bad)
    assert results[1]["status"] == "rolled_back"
    assert heads == ["read"]
    assert bad.read_bytes() == raw
    assert target.read_text() == "before"


def test_generated_regular_file_record_still_recovers(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    session = root / "sessions" / "session"
    journal = CheckpointStore(session)
    target = root / "target.txt"
    target.write_text("before")
    journal.backup_before_edit("turn", str(target))
    target.write_text("after")
    journal.commit_after_edit("turn", str(target))
    plan = journal.plan_rewind_operation(["turn"])
    record = {**prepared(), "actions": plan["actions"]}
    directory = session_backup_root(session) / "intents"
    directory.mkdir()
    path = directory / "record.json"
    path.write_text(json.dumps(record))
    target.write_text("before")
    result = journal.recover_rewind_intents(
        get_head=lambda: "old", compare_and_set_head=lambda *_: pytest.fail("unexpected HEAD update"),
    )
    assert result[0]["status"] == "rolled_back"
    assert target.read_text() == "after"
    assert json.loads(path.read_text())["status"] == "rolled_back"
