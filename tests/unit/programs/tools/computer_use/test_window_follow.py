"""Following a shown window by the operating system's events: which windows are
followed, when the platform source runs, and what each platform's event means."""
from __future__ import annotations

import pytest

from openprogram.host._platform import changes
from openprogram.programs.tools.computer_use.computer_use.desktop import embedding
from openprogram.programs.tools.computer_use.computer_use.desktop.embedding import follow
from openprogram.programs.tools.computer_use.computer_use.desktop.embedding.follow.linux import (CONFIGURE_NOTIFY,
    DESTROY_NOTIFY, PROPERTY_NOTIFY, changed_window)
from openprogram.programs.tools.computer_use.computer_use.desktop.embedding.follow.macos import windows_for
from openprogram.programs.tools.computer_use.computer_use.desktop.embedding.follow.windows import (EVENT_OBJECT_LOCATIONCHANGE,
    EVENT_SYSTEM_FOREGROUND, window_event)


class _Source:
    """A platform source that records what it is asked, with no thread."""

    made = 0

    def __init__(self, report, accept=True):
        _Source.made += 1
        self.report = report
        self.accept = accept
        self.windows: set[int] = set()
        self.closed = False

    def add(self, window_id, pid):
        if self.accept:
            self.windows.add(window_id)
        return self.accept

    def remove(self, window_id):
        self.windows.discard(window_id)

    def close(self):
        self.closed = True


def test_the_source_runs_from_the_first_followed_window_to_the_last():
    sources, told = [], []
    follower = follow.Follower(lambda report: sources.append(_Source(report)) or sources[-1], told.append)
    assert sources == [], "nothing runs before a window is shown"
    assert follower.follow(5, 10) is True
    assert follower.follow(6, 10) is True
    assert follower.follow(5, 10) is True, "following again is the same follow"
    assert len(sources) == 1 and sources[0].windows == {5, 6}

    sources[0].report(5)
    sources[0].report(99)
    assert told == [5], "only followed windows are told"

    follower.unfollow(5)
    assert sources[0].windows == {6} and not sources[0].closed
    sources[0].report(5)
    assert told == [5], "an unfollowed window is quiet"
    follower.unfollow(6)
    assert sources[0].closed, "the last window stops the source"
    assert follower.follow(7, 1) is True and len(sources) == 2, "a new view starts a new one"
    follower.stop()
    assert sources[1].closed and not follower.following(7)


def test_no_events_means_the_view_looks_on_its_own():
    def broken(_report):
        raise RuntimeError("no Accessibility access")

    assert follow.Follower(broken, lambda _w: None).follow(5, 1) is False

    made = []
    follower = follow.Follower(lambda report: made.append(_Source(report, accept=False)) or made[-1],
                               lambda _w: None)
    assert follower.follow(5, 1) is False
    assert made[0].closed, "a source holding no window does not keep running"


def test_platform_events_name_the_window_that_changed():
    # X11: structure events on the window, and only its name or state among properties.
    assert changed_window(CONFIGURE_NOTIFY, 9, 0, {9}, {101}) == 9
    assert changed_window(DESTROY_NOTIFY, 9, 0, {9}, {101}) == 9
    assert changed_window(PROPERTY_NOTIFY, 9, 101, {9}, {101}) == 9
    assert changed_window(PROPERTY_NOTIFY, 9, 555, {9}, {101}) is None
    assert changed_window(CONFIGURE_NOTIFY, 8, 0, {9}, {101}) is None
    # Windows: the window object itself, or it becoming the foreground window.
    assert window_event(EVENT_OBJECT_LOCATIONCHANGE, 9, 0, 0, {9: 1}) == 9
    assert window_event(EVENT_OBJECT_LOCATIONCHANGE, 9, -8, 0, {9: 1}) is None, "its caret moving is not it"
    assert window_event(EVENT_SYSTEM_FOREGROUND, 9, 0, 0, {9: 1}) == 9
    assert window_event(EVENT_OBJECT_LOCATIONCHANGE, 7, 0, 0, {9: 1}) is None
    # macOS: the window an observer names, or every window of its application.
    windows = {1: "a", 2: "b"}
    assert windows_for(windows, "b", lambda x, y: x == y) == [2]
    assert windows_for(windows, "app", lambda x, y: x == y) == [1, 2]


class _Embedder:
    mode = "stream"
    place_input = True
    view_input = True

    def attach(self, window_id, host):
        return {"mode": self.mode, "id": window_id, "pid": 4, "open": True}

    def watch(self, window_id):
        return {"mode": self.mode, "id": window_id, "pid": 4, "open": True}

    def state(self, window_id):
        return {"mode": self.mode, "id": window_id, "pid": 4, "open": True}

    def detach(self, window_id):
        pass


@pytest.fixture
def actions(monkeypatch):
    from openprogram.interfaces.server.ws_actions import embedded_window as module
    monkeypatch.setattr(module, "_holders", {})
    monkeypatch.setattr(module, "_placed", set())
    monkeypatch.setattr(module, "_follower", None)
    yield module
    module.stop_following()


def test_held_windows_are_followed_and_announced(monkeypatch, actions):
    made: list[_Source] = []
    actions.start_following(lambda report: made.append(_Source(report)) or made[-1])
    fake = _Embedder()
    signalled = []
    unsubscribe = changes.subscribe(actions.TOPIC, signalled.append)
    try:
        tab = actions.attach(fake, 5, "tab:a", True, {"rect": [0, 0, 10, 10]}, embedding.parse_host)
        view = actions.attach(fake, 5, "view:b", False, None, embedding.parse_host)
        assert tab["follows"] is True and view["follows"] is True
        made[0].report(5)
        assert signalled == [("5",)], "a change goes to the windows keyed by the window"

        actions.detach(fake, 5, "tab:a")
        assert made[0].windows == {5}, "a view still shows it"
        actions.detach(fake, 5, "view:b")
        assert made[0].closed, "nothing shows it: no events are followed"

        actions.attach(fake, 6, "view:c", False, None, embedding.parse_host)
        fake.state = lambda window_id: {"id": window_id, "open": False}
        actions.state(fake, 6, "view:c")
        assert made[1].closed, "a closed window is not followed"
    finally:
        unsubscribe()


def test_without_following_a_window_has_no_follows_mark(actions):
    window = actions.attach(_Embedder(), 5, "view:a", False, None, embedding.parse_host)
    assert "follows" not in window
