"""Shared durable transaction boundary for project document publication."""

import json
from pathlib import Path

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore


def test_publish_document_writes_and_replays_immutable_receipt(tmp_path: Path) -> None:
    target = tmp_path / "document.bin"
    source = tmp_path / "candidate.bin"
    target.write_bytes(b"before\0")
    source.write_bytes(b"after\xff")
    journal = CheckpointStore(recovery_root=tmp_path / "history")

    result = journal.publish_document(
        "operation-1", target, source,
        expected_revision="37d9c830e637e2c7878943723f87aa2057db902a960353334ab23a57990e72ee",
        fingerprint="request-fingerprint",
    )

    assert result["status"] == "committed"
    assert target.read_bytes() == b"after\xff"
    assert result["before"]["blob_ref"]
    assert result["after"]["blob_ref"]
    assert (tmp_path / "history" / "operations" / "operation-1" / "intent.json").is_file()
    replay = journal.publish_document(
        "operation-1", target, source, fingerprint="request-fingerprint",
    )
    assert replay["transaction_id"] == result["transaction_id"]


def test_publish_document_distinguishes_absent_from_empty_and_rejects_corrupt_intent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "new.bin"
    source = tmp_path / "candidate.bin"
    source.write_bytes(b"value")
    journal = CheckpointStore(recovery_root=tmp_path / "history")
    with pytest.raises(Exception):
        journal.publish_document("op", target, source, expected_revision="" * 64, fingerprint="f")
    assert journal.publish_document("op", target, source, expected_revision="absent", fingerprint="f")["status"] == "committed"

    broken = tmp_path / "history" / "operations" / "broken"
    broken.mkdir(parents=True)
    (broken / "intent.json").write_text("{broken", encoding="utf-8")
    assert journal.read_document_operation("broken")["status"] == "recovery_required"


def test_publish_document_rejects_oversized_source_before_target_change(tmp_path: Path) -> None:
    target = tmp_path / "target.bin"
    source = tmp_path / "source.bin"
    target.write_bytes(b"original")
    with source.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)
    journal = CheckpointStore(recovery_root=tmp_path / "history")
    with pytest.raises(Exception):
        journal.publish_document("large", target, source, fingerprint=json.dumps({"x": 1}))
    assert target.read_bytes() == b"original"
