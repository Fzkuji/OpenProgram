"""File events reach the topic a directory was registered under."""
import time

import pytest

from openprogram.host._platform import changes


@pytest.fixture
def watching():
    changes.start_watching()
    yield
    changes.stop_watching()
    with changes._lock:
        changes._watched.clear()


def _settle():
    time.sleep(1.2)


def test_a_named_file_another_process_writes_wakes_its_topic(tmp_path, watching):
    with changes.listen("stop-file") as listener:
        changes.watch(tmp_path, "stop-file", names={"stop-request"})
        _settle()
        (tmp_path / "unrelated").write_text("x", encoding="utf-8")
        assert listener.wait(2.5) == set()
        (tmp_path / "stop-request").write_text("x", encoding="utf-8")
        assert listener.wait(5.0) == {"stop-file"}


def test_a_tree_is_watched_at_any_depth(tmp_path, watching):
    root = tmp_path / "skills"
    root.mkdir()
    with changes.listen("tree") as listener:
        changes.watch(root, "tree", recursive=True)
        _settle()
        (root / "a" / "b").mkdir(parents=True)
        listener.wait(5.0)
        _settle()
        listener.wait(0)
        (root / "a" / "b" / "SKILL.md").write_text("x", encoding="utf-8")
        assert listener.wait(5.0) == {"tree"}


def test_a_directory_made_later_is_watched_once_it_exists(tmp_path, watching):
    later = tmp_path / "a" / "b"
    with changes.listen("later") as listener:
        changes.watch(later, "later")
        _settle()
        later.mkdir(parents=True)
        listener.wait(5.0)
        _settle()
        listener.wait(0)
        (later / "f").write_text("x", encoding="utf-8")
        assert listener.wait(5.0) == {"later"}


def test_registrations_from_many_threads_at_once_start_each_watcher_once(tmp_path, watching):
    # The Worker registers paths from its server thread and its own at once;
    # each restart used to start the threads another restart had just made.
    import threading

    errors = []
    previous = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)
    try:
        def register(index):
            for step in range(20):
                folder = tmp_path / f"t{index}-{step}"
                folder.mkdir()
                changes.watch(folder, f"topic-{index}", recursive=bool(step % 2))
        workers = [threading.Thread(target=register, args=(index,)) for index in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    finally:
        threading.excepthook = previous
    assert errors == []
    with changes._lock:
        assert all(thread.is_alive() for thread, _stop in changes._watch_threads.values())
