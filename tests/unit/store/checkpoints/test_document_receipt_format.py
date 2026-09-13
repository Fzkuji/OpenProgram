"""Damaged document receipts remain readable as recovery-required outcomes."""
import json
from pathlib import Path

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.fixture
def published(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    journal = CheckpointStore(recovery_root=root / "history")
    target, source = root / "target", root / "source"
    target.write_bytes(b"before")
    source.write_bytes(b"after")
    operation = "a" * 32
    result = journal.publish_document(operation, target, source, fingerprint="f")
    assert result["status"] == "committed"
    path = root / "history" / "operations" / operation / "intent.json"
    return journal, operation, target, source, path


@pytest.mark.parametrize("field,value", [
    ("status", []), ("status", {}), ("status", None), ("status", "unknown"),
    ("before", {"kind": []}), ("after", {"kind": {}}),
])
@pytest.mark.parametrize("retry", [False, True])
def test_invalid_record_fields_preserve_receipt_and_target(published, field, value, retry):
    journal, operation, target, source, path = published
    record = json.loads(path.read_text())
    record[field] = value
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    if retry:
        result = journal.publish_document(operation, target, source, fingerprint="f")
    else:
        result = journal.read_document_operation(operation)
    assert result["status"] == "recovery_required"
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert path.read_bytes() == raw
    assert target.read_bytes() == b"after"


@pytest.mark.parametrize("raw", [b"\xff", b"{", b"[]"])
def test_malformed_text_receipt_preserves_data(published, raw):
    journal, operation, target, _, path = published
    path.write_bytes(raw)
    result = journal.read_document_operation(operation)
    assert result["status"] == "recovery_required"
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert path.read_bytes() == raw
    assert target.read_bytes() == b"after"


@pytest.mark.parametrize("status", ["prepared", "applying", "committed", "rolled_back", "aborted", "recovery_required"])
def test_recognized_receipts_keep_existing_projection(published, status):
    journal, operation, _, _, path = published
    record = json.loads(path.read_text())
    record["status"] = status
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    result = journal.read_document_operation(operation)
    assert result["status"] == ("recovery_required" if status in {"prepared", "applying"} else status)
    assert path.read_bytes() == raw


def test_missing_operational_error_and_cancellation(published, monkeypatch):
    journal, operation, _, _, path = published
    assert journal.read_document_operation("b" * 32)["status"] == "not_found"
    original = Path.read_text
    for error in (PermissionError, KeyboardInterrupt):
        def fail(self, *args, **kwargs):
            if self == path:
                raise error("injected read failure")
            return original(self, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", fail)
        if error is PermissionError:
            assert journal.read_document_operation(operation)["status"] == "recovery_required"
        else:
            with pytest.raises(KeyboardInterrupt):
                journal.read_document_operation(operation)
