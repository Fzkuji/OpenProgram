"""Bounded archival for unpaired group messages."""

from __future__ import annotations

import pathlib


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
    from openprogram.memory import writing
    from openprogram.memory.source_format import scan_source_archive

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
    from openprogram.memory import writing

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
    from openprogram.memory import writing

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
    from openprogram.memory import writing

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    assert _archive(writing, "m1", "") == ""
    assert not (tmp_path / "state/memory/sources").exists()


def test_unpaired_archive_is_off_with_the_backend_disabled(
    tmp_path, monkeypatch,
):
    from openprogram.memory import writing

    monkeypatch.setattr(
        "openprogram.paths.get_state_dir", lambda: tmp_path / "state",
    )
    monkeypatch.setattr("openprogram.memory.is_enabled", lambda: False)
    assert _archive(writing, "m1", "one") == ""
    assert not (tmp_path / "state/memory").exists()


def test_a_rejected_message_leaves_no_partial_state(tmp_path, monkeypatch):
    """A refusal consumes no quota slot and writes no archive bytes."""
    from openprogram.memory import writing
    from openprogram.memory.workspace_layout import runtime_dir

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


def test_concurrent_attempts_cannot_exceed_the_window_quota(tmp_path):
    """Two real processes share one counter, not one per interpreter.

    The reservation is guarded by ``workspace_write_lock``, an ``flock``
    on a file. Threads in one interpreter cannot contend for that lock —
    the same process already holds it — so a threaded version of this
    test passes whether the cross-process lock works or not, and would
    have kept passing if the guard were an in-process ``threading.Lock``.
    Separate interpreters are the only way to exercise what the code
    actually relies on.
    """
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    from openprogram.memory.source_format import scan_source_archive

    workers = 8
    quota = 3
    # Each child archives one message with the same frozen clock and the
    # same quota ceiling, then prints the source ID it managed to claim.
    program = f"""
import sys
from openprogram.memory import writing
import openprogram.paths as paths

paths.get_state_dir = lambda: __import__("pathlib").Path({str(tmp_path / "state")!r})
writing.UNPAIRED_ARCHIVE_MAX_PER_WINDOW = {quota}
writing.time.time = lambda: 1000.0
print(writing.archive_unpaired_group_message(
    channel="telegram", account_id="main", chat_id="group-1",
    message_id=sys.argv[1], user_id="u1", user_display="U",
    text="message " + sys.argv[1], timestamp=1.0,
) or "")
"""

    def attempt(index: int) -> str:
        done = subprocess.run(
            [sys.executable, "-c", program, f"m{index}"],
            capture_output=True, text=True, timeout=120,
            cwd=str(pathlib.Path(__file__).resolve().parents[2]),
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt, range(workers)))

    accepted = [value for value in results if value]
    assert len(accepted) == quota
    assert len(set(accepted)) == quota

    files = list((tmp_path / "state/memory/sources").rglob("*.md"))
    assert len(files) == 1
    relative = files[0].relative_to(tmp_path / "state/memory")
    scan = scan_source_archive(files[0].read_text(encoding="utf-8"), relative)
    assert scan.complete
    assert {frame.source_id for frame in scan.frames} == set(accepted)
