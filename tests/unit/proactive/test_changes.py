"""A change wakes whoever waits on it; nothing looks on a timer."""
from openprogram.host._platform import changes


def test_a_signal_wakes_the_listener_with_its_keys():
    with changes.listen("jobs", "other") as listener:
        changes.signal("jobs", "a", "b")
        changes.signal("unrelated")
        assert listener.wait(0) == {"jobs:a", "jobs:b"}
        assert listener.wait(0) == set()
        changes.signal("other")
        assert listener.wait(0) == {"other"}


def test_a_closed_listener_is_not_told():
    listener = changes.listen("jobs")
    listener.close()
    changes.signal("jobs")
    assert listener.wait(0) == set()


def test_a_subscriber_is_called_on_the_signalling_thread_and_can_leave():
    seen = []
    unsubscribe = changes.subscribe("tables", seen.append)
    changes.signal("tables", "job_admissions")
    unsubscribe()
    changes.signal("tables", "executions")
    assert seen == [("job_admissions",)]


def test_a_failing_subscriber_does_not_break_the_writer():
    unsubscribe = changes.subscribe("tables", lambda keys: 1 / 0)
    try:
        changes.signal("tables")  # does not raise
    finally:
        unsubscribe()


def test_waiting_falls_back_to_its_poll_without_file_events(monkeypatch):
    waited = []
    listener = changes.listen("files")
    monkeypatch.setattr(listener, "wait", lambda timeout: waited.append(timeout) or set())
    monkeypatch.setattr(changes, "file_events_available", lambda: False)
    changes.wait_for(listener, resync=300, poll=2)
    monkeypatch.setattr(changes, "file_events_available", lambda: True)
    changes.wait_for(listener, resync=300, poll=2)
    listener.close()
    assert waited == [2, 300]


def test_a_due_time_shortens_the_wait(monkeypatch):
    waited = []
    listener = changes.listen("due")
    monkeypatch.setattr(listener, "wait", lambda timeout: waited.append(timeout) or set())
    monkeypatch.setattr(changes.time, "time", lambda: 1000.0)
    changes.wait_for(listener, resync=300, due=1030.0)
    changes.wait_for(listener, resync=300, due=900.0)
    listener.close()
    assert waited == [30.0, 0.0]


def test_published_topics_reach_windows_as_one_frame_per_topic(monkeypatch):
    frames, scheduled = [], []
    changes.publish("conversation.test")
    changes.set_publisher(lambda delay, callback: scheduled.append(callback), frames.append)
    try:
        changes.signal("conversation.test", "s1")
        changes.signal("conversation.test", "s2")
        changes.signal("unpublished.test")
        assert len(scheduled) == 1
        scheduled[0]()
        assert frames == [{"type": "changed", "topic": "conversation.test", "keys": ["s1", "s2"]}]
        changes.signal("conversation.test")
        scheduled[1]()
        assert frames[-1] == {"type": "changed", "topic": "conversation.test", "keys": None}
    finally:
        changes.set_publisher(None)


def test_without_a_publisher_nothing_is_queued(monkeypatch):
    frames = []
    changes.publish("conversation.quiet")
    changes.signal("conversation.quiet", "s1")
    assert frames == []
    assert "conversation.quiet" not in changes._publish_pending


def test_a_watched_folder_changing_only_its_own_metadata_wakes_no_one(tmp_path):
    """A store that chmods its folder on every lock must not wake its own loop."""
    folder = tmp_path / "store"
    folder.mkdir()
    changes.watch(folder, "store.files", recursive=True)
    for entry in changes._watched:  # anchored as the watcher threads would, which unit tests do not start
        entry.anchor = changes._anchor(entry.path)
    try:
        with changes.listen("store.files") as listener:
            changes._dispatch([changes._real(folder)])
            assert listener.wait(0) == set()
            changes._dispatch([changes._real(folder) / "record.json"])
            assert listener.wait(0) == {"store.files"}
    finally:
        changes.unwatch(folder, "store.files")


