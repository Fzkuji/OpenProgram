"""Bounded archival for unpaired group messages."""

from __future__ import annotations


def _archive(writing, message_id: str, text: str) -> str:
    return writing.archive_unpaired_group_message(
        channel="telegram",
        account_id="main",
        chat_id="group-1",
        message_id=message_id,
        user_id="u1",
        user_display="U",
        text=text,
        timestamp=1.0,
    )


def test_unpaired_archive_refuses_messages_after_global_rate_limit(
    tmp_path, monkeypatch,
):
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.source_format import scan_source_archive

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(writing, "UNPAIRED_ARCHIVE_MAX_PER_WINDOW", 2)
    monkeypatch.setattr(writing.time, "time", lambda: 1000.0)

    assert _archive(writing, "m1", "one")
    assert _archive(writing, "m2", "two")
    assert _archive(writing, "m3", "three") == ""

    files = list((tmp_path / "state/memory/sources").rglob("*.md"))
    assert len(files) == 1
    relative = files[0].relative_to(tmp_path / "state/memory")
    scan = scan_source_archive(files[0].read_text(encoding="utf-8"), relative)
    assert scan.complete and len(scan.frames) == 2


def test_unpaired_archive_refuses_a_message_that_exceeds_storage_quota(
    tmp_path, monkeypatch,
):
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(writing, "UNPAIRED_ARCHIVE_MAX_TOTAL_BYTES", 8)
    monkeypatch.setattr(writing, "UNPAIRED_ARCHIVE_MAX_MESSAGE_BYTES", 8)
    monkeypatch.setattr(writing, "UNPAIRED_ARCHIVE_FRAME_RESERVE_BYTES", 0)

    assert _archive(writing, "m1", "1234")
    assert _archive(writing, "m2", "56789") == ""

    archived = next(
        (tmp_path / "state/memory/sources").rglob("*.md")
    ).read_text(encoding="utf-8")
    assert "1234" in archived
    assert "56789" not in archived


def test_unpaired_archive_refuses_an_oversized_identity(tmp_path, monkeypatch):
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    assert writing.archive_unpaired_group_message(
        channel="telegram",
        account_id="main",
        chat_id="group-1",
        message_id="m1",
        user_id="u" * (writing.UNPAIRED_ARCHIVE_MAX_IDENTITY_BYTES + 1),
        user_display="U",
        text="hello",
        timestamp=1.0,
    ) == ""
    assert not (tmp_path / "state/memory/sources").exists()


def test_unpaired_archive_refuses_empty_text(tmp_path, monkeypatch):
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    assert _archive(writing, "m1", "") == ""
    assert not (tmp_path / "state/memory/sources").exists()


def test_unpaired_archive_is_off_with_the_backend_disabled(
    tmp_path, monkeypatch,
):
    from openprogram.memory.scriptorium import writing

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr("openprogram.memory.is_enabled", lambda: False)
    assert _archive(writing, "m1", "one") == ""
    assert not (tmp_path / "state/memory").exists()


def test_a_rejected_message_leaves_no_partial_state(tmp_path, monkeypatch):
    """A refusal consumes no quota slot and writes no archive bytes."""
    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.workspace_layout import runtime_dir

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(writing, "UNPAIRED_ARCHIVE_MAX_PER_WINDOW", 1)
    monkeypatch.setattr(writing.time, "time", lambda: 1000.0)

    assert _archive(writing, "m1", "accepted")
    root = tmp_path / "state/memory"
    quota = runtime_dir(root) / writing._UNPAIRED_ARCHIVE_QUOTA_FILE
    accepted_before = quota.read_text(encoding="utf-8")
    archived_before = sorted(
        (p, p.read_bytes()) for p in (root / "sources").rglob("*.md")
    )

    assert _archive(writing, "m2", "refused") == ""
    assert quota.read_text(encoding="utf-8") == accepted_before
    assert sorted(
        (p, p.read_bytes()) for p in (root / "sources").rglob("*.md")
    ) == archived_before


def test_concurrent_attempts_cannot_exceed_the_window_quota(
    tmp_path, monkeypatch,
):
    """The reservation happens under the workspace write lock, so parallel
    senders share one counter rather than each seeing the last free slot."""
    import threading

    from openprogram.memory.scriptorium import writing
    from openprogram.memory.scriptorium.source_format import scan_source_archive

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr(writing, "UNPAIRED_ARCHIVE_MAX_PER_WINDOW", 3)
    monkeypatch.setattr(writing.time, "time", lambda: 1000.0)

    accepted: list[str] = []
    guard = threading.Lock()
    start = threading.Barrier(8)

    def attempt(index: int) -> None:
        start.wait()
        result = _archive(writing, f"m{index}", f"message {index}")
        if result:
            with guard:
                accepted.append(result)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert len(accepted) == 3
    assert len(set(accepted)) == 3
    files = list((tmp_path / "state/memory/sources").rglob("*.md"))
    assert len(files) == 1
    relative = files[0].relative_to(tmp_path / "state/memory")
    scan = scan_source_archive(files[0].read_text(encoding="utf-8"), relative)
    assert scan.complete
    assert {frame.source_id for frame in scan.frames} == set(accepted)
