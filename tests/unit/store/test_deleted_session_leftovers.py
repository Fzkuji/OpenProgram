"""What a deleted conversation leaves on disk.

A delete keeps one thing: the delete mark in ``.deleted/``, which is what
stops the id from coming back. Its lock files and the file history only it
wrote go with it, and a store that opens removes the lock files of ids it
has no conversation for.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openprogram.host._platform import locking
from openprogram.state.store.session import session_lock
from openprogram.state.store.session.session_store import SessionStore
from openprogram.state.store.shadow_git import store as shadow


def _store(root: Path) -> SessionStore:
    store = SessionStore(root)
    for session_id in ("kept", "gone"):
        store.create_session(session_id, "main")
        store.append_message(session_id, {"id": "u1", "role": "user", "content": "hi"})
    return store


def _swept(store: SessionStore) -> SessionStore:
    """``store`` once the removals its opening started have finished."""
    if store._leftover_sweep is not None:
        store._leftover_sweep.join(timeout=30)
    return store


def _lock_names(root: Path) -> set[str]:
    return {path.name for path in (root / ".locks").iterdir()}


def test_delete_removes_the_lock_files_and_keeps_the_mark(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    store = _store(root)
    goal = root / ".goal-locks" / f"{hashlib.sha256(b'gone').hexdigest()}.lock"
    goal.parent.mkdir()
    goal.touch()
    with session_lock.registry_file_lock(root, "operations-gone"):
        pass
    assert {"gone.lock", "kept.lock", ".operations-gone.lock"} <= _lock_names(root)

    store.delete_session("gone")

    names = _lock_names(root)
    assert "gone.lock" not in names and ".operations-gone.lock" not in names
    assert "kept.lock" in names
    assert not goal.exists()
    assert (root / ".deleted" / "gone").is_file()
    assert store.get_session("gone") is None


def test_a_removed_lock_file_is_not_the_one_a_waiter_holds(tmp_path: Path) -> None:
    """Two holders is the danger of removing a lock file: whoever opened it
    before the removal must notice and take the file at the path."""
    path = tmp_path / "a.lock"
    with open(path, "a+", encoding="utf-8") as waiter:
        assert session_lock.lock_file_current(waiter, path)
        assert session_lock.remove_lock_file(path)
        assert not session_lock.lock_file_current(waiter, path)
        path.touch()
        assert not session_lock.lock_file_current(waiter, path)


def test_a_held_lock_file_is_not_removed(tmp_path: Path) -> None:
    path = tmp_path / "a.lock"
    with open(path, "a+", encoding="utf-8") as holder:
        locking.flock(holder.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        assert not session_lock.remove_lock_file(path)
        assert path.exists()
        locking.flock(holder.fileno(), locking.LOCK_UN)
    assert session_lock.remove_lock_file(path)
    assert session_lock.remove_lock_file(path)  # already gone


def test_a_conversation_lock_taken_after_its_file_was_removed_is_on_the_path(
        tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    with session_lock.session_interprocess_lock("s1", root=root):
        (root / ".locks" / "s1.lock").unlink()
    with session_lock.session_interprocess_lock("s1", root=root):
        lock = session_lock.conversation_lock("s1", root)
        assert session_lock.lock_file_current(lock._handle, lock.path)


def test_opening_a_store_sweeps_lock_files_of_ids_it_does_not_have(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    store = _store(root)
    locks = root / ".locks"
    goal_locks = root / ".goal-locks"
    goal_locks.mkdir()
    kept_goal = goal_locks / f"{hashlib.sha256(b'kept').hexdigest()}.lock"
    stray_goal = goal_locks / f"{hashlib.sha256(b'probed').hexdigest()}.lock"
    for path in (locks / "probed.lock", locks / ".operations-probed.lock",
                 locks / ".operations-kept.lock", locks / ".migration-probed.lock",
                 locks / "held.lock", kept_goal, stray_goal):
        path.touch()
    registry = {name for name in _lock_names(root) if name.startswith(".")
                and not name.startswith((".operations-", ".migration-probed"))}
    assert registry  # the store's own registry locks
    del store

    with open(locks / "held.lock", "a+", encoding="utf-8") as holder:
        locking.flock(holder.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        _swept(SessionStore(root))
        locking.flock(holder.fileno(), locking.LOCK_UN)

    names = _lock_names(root)
    assert {"kept.lock", "gone.lock", ".operations-kept.lock", "held.lock"} <= names
    assert registry <= names
    assert not {"probed.lock", ".operations-probed.lock", ".migration-probed.lock"} & names
    assert kept_goal.exists() and not stray_goal.exists()


@pytest.fixture
def shadow_root(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: state)
    return state / "shadow-git"


def _repo(shadow_root: Path, name: str, owners: list[str] | None) -> Path:
    repo = shadow_root / name
    (repo / ".git").mkdir(parents=True)
    (repo / "notes.md").write_text("copy", encoding="utf-8")
    if owners is not None:
        (repo / ".git" / shadow.OWNERS_DIR).mkdir()
        for owner in owners:
            (repo / ".git" / shadow.OWNERS_DIR / owner).touch()
    return repo


def test_file_history_goes_with_the_last_conversation_that_wrote_it(shadow_root: Path) -> None:
    own = _repo(shadow_root, "own", ["gone"])
    shared_repo = _repo(shadow_root, "shared", ["gone", "kept"])
    other = _repo(shadow_root, "other", ["kept"])
    unlisted = _repo(shadow_root, "unlisted", None)

    assert shadow.release_session("gone") == 1

    assert not own.exists()
    assert shared_repo.exists() and shadow._owners(shared_repo) == {"kept"}
    assert other.exists() and unlisted.exists()

    assert shadow.release_session("kept") == 2
    assert not shared_repo.exists() and not other.exists()
    assert unlisted.exists()


def test_deleting_a_conversation_releases_its_file_history(shadow_root, monkeypatch) -> None:
    from openprogram.runtime import lower_layers
    from openprogram.state.store.session import default_store, session_store

    monkeypatch.setattr(session_store.shared, "_default_store", None)
    lower_layers.register()
    store = default_store()
    store.create_session("gone", "main")
    store.append_message("gone", {"id": "u1", "role": "user", "content": "hi"})
    own = _repo(shadow_root, "own", ["gone"])

    store.delete_session("gone")

    assert not own.exists()
    assert not (shadow_root.parent / "projects" / ".locks" / "gone.lock").exists()


def test_opening_the_store_releases_history_a_stopped_delete_left(shadow_root, monkeypatch) -> None:
    from openprogram.runtime import lower_layers
    from openprogram.state.store.session import default_store, session_store
    from openprogram.state.store.session.placement import record_delete_intent

    projects = shadow_root.parent / "projects"
    record_delete_intent(projects, "gone", {"session_id": "gone"})
    left = _repo(shadow_root, "left", ["gone"])
    live = _repo(shadow_root, "live", ["gone", "kept"])

    monkeypatch.setattr(session_store.shared, "_default_store", None)
    lower_layers.register()
    _swept(default_store())

    assert not left.exists()
    assert shadow._owners(live) == {"kept"}


def test_the_sweep_is_not_run_by_the_thread_that_opens_the_store(tmp_path: Path, monkeypatch) -> None:
    """A store can be opened on a server's event loop: opening lists the
    leftovers and a thread of its own removes them."""
    import threading

    root = tmp_path / "sessions"
    _store(root)
    (root / ".locks" / "probed.lock").touch()
    removers: list[str] = []
    remove = session_lock.remove_lock_file
    monkeypatch.setattr(session_lock, "remove_lock_file",
                        lambda path: removers.append(threading.current_thread().name) or remove(path))

    store = SessionStore(root)
    # A lock file made after the store opened is not a leftover of this pass.
    (root / ".locks" / "later.lock").touch()
    _swept(store)

    assert removers == ["openprogram-leftover-sweep"]
    assert "probed.lock" not in _lock_names(root) and "later.lock" in _lock_names(root)


def test_a_store_with_nothing_left_over_starts_no_sweep(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    _store(root)
    assert SessionStore(root)._leftover_sweep is None


def test_a_live_conversation_keeps_its_folder_when_its_idle_lock_file_is_swept(tmp_path: Path) -> None:
    """The index a store opens with can miss a conversation another process
    just made. Its lock file is all the sweep can take, and the next lock
    of the id makes that again."""
    root = tmp_path / "sessions"
    store = _store(root)
    kept_dir = store._session_dir("kept")
    before = sorted(path.name for path in kept_dir.iterdir())

    session_lock.sweep_lock_files(session_lock.stale_lock_files(root, known_session_ids=()))

    assert "kept.lock" not in _lock_names(root)
    assert sorted(path.name for path in kept_dir.iterdir()) == before
    assert [m["id"] for m in SessionStore(root).get_messages("kept")] == ["u1"]
    store.append_message("kept", {"id": "a1", "role": "assistant", "content": "again", "predecessor": "u1"})
    assert "kept.lock" in _lock_names(root)


def test_a_conversation_that_lists_itself_keeps_the_history_from_a_delete(shadow_root: Path) -> None:
    """Deleting the last listed conversation while another one starts
    writing: the one that got listed keeps the repository."""
    repo = _repo(shadow_root, "shared", ["gone"])
    unlink = shadow.os.unlink

    def unlink_then_another_lists_itself(path):
        unlink(path)
        (repo / ".git" / shadow.OWNERS_DIR / "arrived").touch()

    shadow.os.unlink = unlink_then_another_lists_itself
    try:
        assert shadow.release_session("gone") == 0
    finally:
        shadow.os.unlink = unlink
    assert shadow._owners(repo) == {"arrived"} and (repo / "notes.md").exists()


def test_history_of_another_stores_conversations_is_not_this_stores_to_remove(shadow_root: Path) -> None:
    """Only a delete mark of the store that sweeps counts: a repository
    listing an id this store never had, or has, stays."""
    foreign = _repo(shadow_root, "foreign", ["elsewhere"])
    mixed = _repo(shadow_root, "mixed", ["gone", "elsewhere"])
    stopped = _repo(shadow_root, "stopped" + shadow._REMOVED_SUFFIX, None)

    assert shadow.sweep_released(lambda owner: owner == "gone", shadow_root) == 0

    assert foreign.exists() and shadow._owners(mixed) == {"elsewhere"}
    # A removal that stopped after its rename is finished.
    assert not stopped.exists()
