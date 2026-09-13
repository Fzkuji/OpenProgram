"""Direct reads and retries retain malformed rewind receipts."""
import json
from pathlib import Path

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{", b"\xff", b'{"status": []}'])
@pytest.mark.parametrize("entry", ["read", "retry"])
def test_existing_bad_receipt_is_reported_without_replacement(tmp_path, raw, entry):
    journal = CheckpointStore(tmp_path.resolve() / "sessions" / "s")
    path = journal._rewind_intent_path("key")
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)

    def no_head(*_):
        pytest.fail("malformed receipt must not access HEAD")

    if entry == "read":
        result = journal.read_rewind_intent("key")
    else:
        result = journal.apply_rewind_operation(
            [], expected_head_id="old", target_head_id="new", get_head=no_head,
            compare_and_set_head=no_head, idempotency_key="key",
        )
        assert result["head_changed"] is False
    assert result["status"] == "recovery_required"
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert result["intent_path"] == str(path)
    assert result["restored_paths"] == []
    assert path.read_bytes() == raw


def test_read_missing_and_normalize_valid_receipt_without_writing(tmp_path):
    journal = CheckpointStore(tmp_path.resolve() / "sessions" / "s")
    assert journal.read_rewind_intent("missing") is None
    path = journal._rewind_intent_path("key")
    path.parent.mkdir(parents=True)
    value = {"status": "recovery_required", "actions": [],
             "expected_head_id": "old", "target_head_id": "new"}
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    result = journal.read_rewind_intent("key")
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert path.read_bytes() == raw


@pytest.mark.parametrize("entry", ["read", "retry"])
def test_unreadable_receipt_is_not_treated_as_absent(tmp_path, monkeypatch, entry):
    journal = CheckpointStore(tmp_path.resolve() / "sessions" / "s")
    path = journal._rewind_intent_path("key")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"preserve")
    original = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self == path:
            raise PermissionError("receipt unreadable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(PermissionError, match="receipt unreadable"):
        if entry == "read":
            journal.read_rewind_intent("key")
        else:
            journal.apply_rewind_operation(
                [], expected_head_id="old", target_head_id="new",
                get_head=lambda: pytest.fail("unexpected HEAD read"),
                compare_and_set_head=lambda *_: pytest.fail("unexpected HEAD update"),
                idempotency_key="key",
            )
    assert path.read_bytes() == b"preserve"


def test_valid_terminal_receipt_replays_without_execution(tmp_path):
    journal = CheckpointStore(tmp_path.resolve() / "sessions" / "s")
    path = journal._rewind_intent_path("key")
    path.parent.mkdir(parents=True)
    value = {"status": "committed", "actions": [], "expected_head_id": "old",
             "target_head_id": "new", "transaction_id": "tx", "target_msg_id": "message"}
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    result = journal.apply_rewind_operation(
        [], expected_head_id="old", target_head_id="new", idempotency_key="key",
        target_msg_id="message", get_head=lambda: pytest.fail("unexpected HEAD read"),
        compare_and_set_head=lambda *_: pytest.fail("unexpected HEAD update"),
    )
    assert result["status"] == "committed"
    assert result["transaction_id"] == "tx"
    assert result["replayed"] is True
    assert result["head_changed"] is False
    assert path.read_bytes() == raw
