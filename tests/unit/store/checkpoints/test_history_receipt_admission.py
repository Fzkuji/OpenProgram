"""Malformed history receipts must never authorize another file mutation."""
import json
from pathlib import Path

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.fixture
def history(tmp_path):
    root = tmp_path.resolve()
    journal = CheckpointStore(root / "session")
    target = root / "file.txt"
    target.write_text("before")
    journal.backup_before_edit("turn", str(target))
    target.write_text("after")
    journal.commit_after_edit("turn", str(target), operation="edit")
    path = journal._intent_path("turn", "revert", "key")
    path.parent.mkdir(parents=True)
    return journal, target, path


def invoke(journal, entry):
    if entry == "read":
        return journal.read_history_intent("turn", "revert", "key")
    if entry == "retry":
        return journal.apply_history_operation("turn", "revert", idempotency_key="key")
    return journal.recover_history_intents()[0]


@pytest.mark.parametrize("raw", [b"{", b"[]", b"\xff", b'{"status": []}',
    b'{"status":"committed","actions":[{}]}'])
@pytest.mark.parametrize("entry", ["read", "retry", "scan"])
def test_bad_receipt_preserves_record_and_file(history, raw, entry):
    journal, target, path = history
    path.write_bytes(raw)
    result = invoke(journal, entry)
    assert result["status"] == "recovery_required"
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert result["intent_path"] == str(path)
    assert result["restored_paths"] == []
    assert path.read_bytes() == raw
    assert target.read_text() == "after"


@pytest.mark.parametrize("entry", ["read", "retry", "scan"])
@pytest.mark.parametrize("error", [PermissionError, KeyboardInterrupt])
def test_unreadable_receipt_does_not_authorize_mutation(history, monkeypatch, entry, error):
    journal, target, path = history
    path.write_bytes(b"preserve")
    original = Path.read_text

    def fail(self, *args, **kwargs):
        if self == path:
            raise error("injected receipt failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail)
    with pytest.raises(error, match="injected receipt failure"):
        invoke(journal, entry)
    assert path.read_bytes() == b"preserve"
    assert original(target) == "after"


def test_missing_read_and_readonly_normalization(history):
    journal, _, path = history
    assert journal.read_history_intent("turn", "revert", "key") is None
    raw = json.dumps({"status": "recovery_required", "actions": []}).encode()
    path.write_bytes(raw)
    assert invoke(journal, "read")["error_code"] == "RECOVERY_REQUIRED"
    assert path.read_bytes() == raw


def test_scan_continues_and_terminalizes_valid_receipt(history):
    journal, target, path = history
    path.write_bytes(b"{")
    other = path.with_name("z-valid.json")
    other.write_text(json.dumps({"status": "applying", "actions": []}))
    results = journal.recover_history_intents()
    assert len(results) == 2
    assert all(result["status"] == "recovery_required" for result in results)
    assert json.loads(other.read_text())["status"] == "recovery_required"
    assert path.read_bytes() == b"{"
    assert target.read_text() == "after"


def test_valid_terminal_receipt_replays_without_execution(history):
    journal, target, path = history
    first = journal.apply_history_operation("turn", "revert", idempotency_key="key")
    assert first["status"] == "committed"
    raw = path.read_bytes()
    target.write_text("external")
    replay = invoke(journal, "retry")
    assert replay == first
    assert target.read_text() == "external"
    assert path.read_bytes() == raw
