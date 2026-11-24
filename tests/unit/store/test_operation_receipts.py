"""A durable write's receipt reaches the windows when it changes, so a
window whose answer was lost asks for it then instead of on a timer."""
from __future__ import annotations

import inspect
from pathlib import Path

from openprogram.host._platform import changes
from openprogram.state.store import operation_receipts
from openprogram.state.store.file_operations import FileOperationStore


def test_the_receipt_topic_is_published_to_windows():
    assert operation_receipts.TOPIC == "operation.receipts"
    assert operation_receipts.TOPIC in changes._published


def test_a_file_operation_announces_its_key_once_it_is_final(tmp_path: Path):
    store = FileOperationStore(tmp_path / "file_operations.jsonl")
    row, owner = store.begin("p1", "project_file_write", "key-1", "fingerprint")
    assert owner
    with changes.listen(operation_receipts.TOPIC) as listener:
        store.mark_applying(row["operation_id"])
        assert listener.wait(0) == set(), "a step on the way is not a receipt"
        store.complete(row["operation_id"], {"ok": True})
        assert listener.wait(0) == {f"{operation_receipts.TOPIC}:key-1"}
        store.complete(row["operation_id"], {"ok": True})
        assert listener.wait(0) == set(), "a final receipt is announced once"


def test_a_recovery_receipt_is_announced_too(tmp_path: Path):
    store = FileOperationStore(tmp_path / "file_operations.jsonl")
    row, _ = store.begin("p1", "project_file_delete", "key-2", "fingerprint")
    with changes.listen(operation_receipts.TOPIC) as listener:
        store.finish(row["operation_id"], {"error": "gone"}, status="recovery_required",
                     phase="recovery_required")
        assert listener.wait(0) == {f"{operation_receipts.TOPIC}:key-2"}


def test_empty_keys_are_not_announced():
    with changes.listen(operation_receipts.TOPIC) as listener:
        operation_receipts.changed(None)
        operation_receipts.changed("")
        assert listener.wait(0) == set()


def test_turn_reverts_and_reapplies_announce_their_key_however_they_end():
    from openprogram.interfaces.server.ws_actions.turn_files import history

    for handler in (history.handle_revert_turn, history.handle_reapply_turn):
        source = inspect.getsource(handler)
        finally_part = source.split("finally:", 1)[1]
        assert 'operation_receipts.changed(cmd.get("idempotency_key"))' in finally_part

