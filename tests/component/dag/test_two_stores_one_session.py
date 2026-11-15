from pathlib import Path

from openprogram.state.store.session.session_log import SessionLog


def test_our_write_does_not_hide_an_earlier_foreign_write(tmp_path: Path) -> None:
    """Two stores in one process index the same session; neither may hide the other's node.

    The reply placeholder and the context snapshot of one turn are written
    through different stores. Stamping the snapshot's write as "synced"
    made its store skip the placeholder forever, and the resume after an
    approval failed with "continuation assistant placeholder is missing".
    """
    first, second = SessionLog(tmp_path / "s1"), SessionLog(tmp_path / "s1")
    first.write_history(0, "user", "u1", {"id": "u1"})
    first.mark_synced()
    second.mark_synced()

    first.write_history(1, "llm", "reply", {"id": "reply"})
    second.write_history(2, "call", "snapshot", {"id": "snapshot"})

    assert second.stale()


def test_our_own_write_keeps_the_index_synced(tmp_path: Path) -> None:
    session = SessionLog(tmp_path / "s1")
    session.write_history(0, "user", "u1", {"id": "u1"})
    session.mark_synced()
    session.write_history(1, "llm", "reply", {"id": "reply"})
    session.write_meta({"head_id": "reply"})

    assert not session.stale()
