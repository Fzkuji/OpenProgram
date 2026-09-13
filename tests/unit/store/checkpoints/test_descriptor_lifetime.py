"""Public checkpoint operations release descriptors when OS boundaries fail."""
from pathlib import Path
import os

import pytest

from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.store.snapshot.checkpoint import store as checkpoint_store

pytestmark = pytest.mark.skipif(
    not checkpoint_store._DIR_FD_APPLY_SUPPORTED, reason="requires directory descriptors",
)


@pytest.fixture
def directory_handles(monkeypatch):
    real_open, real_close, real_fstat = os.open, os.close, os.fstat
    active = set()

    def tracked_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if flags & os.O_DIRECTORY:
            active.add(fd)
        return fd

    def tracked_close(fd):
        result = real_close(fd)
        active.discard(fd)
        return result

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", tracked_close)
    try:
        yield active, real_fstat
    finally:
        # A failing assertion must not leak descriptors into other tests.
        for fd in tuple(active):
            real_close(fd)
            active.discard(fd)


@pytest.mark.parametrize("validation", [1, 2], ids=["root", "child"])
@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_history_plan_closes_unaccepted_directories(
    tmp_path: Path, monkeypatch, directory_handles, validation, failure,
):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    target = root / "document.txt"
    target.write_text("before")
    journal = CheckpointStore(root / "sessions" / "s")
    journal.backup_before_edit("turn", str(target))
    target.write_text("after")
    journal.commit_after_edit("turn", str(target))
    active, real_fstat = directory_handles
    count = 0

    def fail_validation(fd):
        nonlocal count
        if fd in active:
            count += 1
            if count == validation:
                raise failure("directory validation failed")
        return real_fstat(fd)

    with monkeypatch.context() as injected:
        injected.setattr(os, "fstat", fail_validation)
        if failure is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt, match="directory validation"):
                journal.plan_history_operation("turn", "revert")
        else:
            assert journal.plan_history_operation("turn", "revert")["status"] == "blocked"
    assert not active
    assert target.read_text() == "after"
    assert journal.plan_history_operation("turn", "revert")["status"] == "ready"
    assert not active


@pytest.mark.parametrize("existing", [False, True])
def test_publication_closes_parent_even_when_temporary_cleanup_fails(
    tmp_path: Path, monkeypatch, directory_handles, existing,
):
    root = tmp_path.resolve()
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root / "state")
    target, source = root / "target.txt", root / "source.txt"
    if existing:
        target.write_text("before")
    source.write_text("after")
    journal = CheckpointStore(recovery_root=root / "history")
    active, _ = directory_handles
    real_link, real_unlink = os.link, os.unlink
    link_failed = cleanup_failed = False

    def fail_publication(src, dst, *args, **kwargs):
        nonlocal link_failed
        if not link_failed and str(src).endswith(".tmp"):
            link_failed = True
            raise OSError("publication failed")
        return real_link(src, dst, *args, **kwargs)

    def fail_cleanup(path, *args, **kwargs):
        nonlocal cleanup_failed
        if link_failed and not cleanup_failed and str(path).endswith(".tmp"):
            cleanup_failed = True
            raise OSError("temporary cleanup failed")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as injected:
        injected.setattr(os, "link", fail_publication)
        injected.setattr(os, "unlink", fail_cleanup)
        result = journal.publish_document("a" * 32, target, source, fingerprint="first")
    assert link_failed and cleanup_failed
    assert result["status"] == ("recovery_required" if existing else "rolled_back")
    if existing:
        guards = list(root.glob(".target.txt.*.guard"))
        assert len(guards) == 1
        assert guards[0].read_text() == "before"
        assert journal.read_document_operation("a" * 32)["status"] == "recovery_required"
    assert not target.exists()
    assert not active
    result = journal.publish_document("b" * 32, target, source, fingerprint="second")
    assert result["status"] == "committed"
    assert target.read_text() == "after"
    assert not active
