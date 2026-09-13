"""An unreadable sequence counter cannot authorize duplicate mutation ordering."""
from pathlib import Path

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore


@pytest.mark.parametrize("failure", ["permission", "cancel", b"invalid", b"", b"-1", b"\xff"])
def test_failed_counter_read_preserves_order_and_prepared_receipt(tmp_path, monkeypatch, failure):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    journal = CheckpointStore(root / "session")
    target = root / "target"
    target.write_bytes(b"one")
    journal.backup_before_edit("first", str(target))
    target.write_bytes(b"two")
    journal.commit_after_edit("first", str(target))
    first_sequence = journal.list_mutations("first")[0]["mutation_sequence"]
    counter = root / "state" / "mutation-locks" / "workspace-sequence"
    durable = counter.read_bytes()
    journal.backup_before_edit("second", str(target))
    target.write_bytes(b"three")
    if isinstance(failure, bytes):
        counter.write_bytes(failure)
    original_bytes = counter.read_bytes()
    original_read = Path.read_text
    error = PermissionError if failure == "permission" else KeyboardInterrupt if failure == "cancel" else ValueError

    def fail_read(self, *args, **kwargs):
        if self == counter and not isinstance(failure, bytes):
            raise error("injected counter read failure")
        return original_read(self, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "read_text", fail_read)
        with pytest.raises(error):
            journal.commit_after_edit("second", str(target))
    assert counter.read_bytes() == original_bytes
    assert journal.list_mutations("second") == []
    assert journal.list_file_history("second")[0]["recoverability"] == "unavailable"
    assert target.read_bytes() == b"three"
    # Model resolution of the unreadable/corrupt counter before retrying.
    counter.write_bytes(durable)
    journal.commit_after_edit("second", str(target))
    assert journal.list_mutations("second")[0]["mutation_sequence"] == first_sequence + 1
