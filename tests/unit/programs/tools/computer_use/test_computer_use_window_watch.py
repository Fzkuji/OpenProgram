"""The operated window stays under a live capture so macOS marks it, then is released."""
import threading

from openprogram.programs.tools.computer_use.computer_use_agent.desktop import window_watch


# Threads alive before a test's watch started; _settle waits for the rest.
_threads_before_test: set[threading.Thread] = set()


def _fake(monkeypatch):
    _threads_before_test.clear()
    _threads_before_test.update(threading.enumerate())
    opened, stopped = [], []
    ready = threading.Event()

    def fake_open(window_id):
        opened.append(window_id)
        ready.set()
        return f"stream-{window_id}"

    def fake_stop(watch):
        if watch.stream is not None:
            stopped.append(watch.stream)
            watch.stream = None

    monkeypatch.setattr(window_watch, "_open", fake_open)
    monkeypatch.setattr(window_watch, "_stop", fake_stop)
    return opened, stopped, ready


def _settle():
    """Wait for the start/stop threads the watch began during this test.

    Threads that were already running belong to someone else in the process
    and are left alone; the ceiling only guards against a hang.
    """
    for thread in threading.enumerate():
        if (thread not in _threads_before_test and thread.daemon
                and not isinstance(thread, threading.Timer)):
            thread.join(30)


def test_steps_on_one_window_keep_one_capture(monkeypatch):
    opened, stopped, _ = _fake(monkeypatch)
    watch = window_watch.WindowWatch()
    watch.touch("s", 7)
    watch.touch("s", 7)
    _settle()
    assert opened == [7]
    watch.touch("s", 9)
    _settle()
    assert opened == [7, 9]
    assert stopped == ["stream-7"]
    watch.release("s")
    assert stopped == ["stream-7", "stream-9"]


def test_an_idle_window_is_released(monkeypatch):
    opened, stopped, _ = _fake(monkeypatch)
    watch = window_watch.WindowWatch()
    watch.touch("s", 7)
    _settle()
    watch._watches["s"].last -= window_watch.IDLE_SECONDS
    watch._timer.cancel()
    watch._timer = None
    watch._sweep()
    assert stopped == ["stream-7"]
    assert not watch._watches


def test_no_window_watches_nothing(monkeypatch):
    opened, _, _ = _fake(monkeypatch)
    window_watch.WindowWatch().touch("s", 0)
    _settle()
    assert opened == []


def _row(number, x, y, w, h, name="", alpha=1.0, layer=0):
    return {"kCGWindowNumber": number, "kCGWindowName": name, "kCGWindowAlpha": alpha, "kCGWindowLayer": layer,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


def test_a_window_place_counts_the_windows_in_front_of_it():
    rows = [  # front to back
        _row(1, 0, 0, 50, 50),                                   # far away: does not matter
        _row(5, 0, 0, 1512, 982, layer=25),                      # a system layer over the whole screen
        _row(2, 0, 0, 3000, 3000, name=window_watch.OVERLAY_TITLE),  # the App's own overlay
        _row(3, 350, 150, 100, 100),                             # covers a corner
        _row(4, 380, 120, 40, 40, alpha=0),                      # invisible
        _row(7, 100, 200, 400, 300),                             # the controlled window
        _row(8, 120, 220, 100, 100),                             # behind it
    ]
    assert window_watch.window_place(7, rows) == {"frame": [100, 200, 400, 300], "occluders": [[350, 150, 100, 100]]}
    assert window_watch.window_place(99, rows) is None


def test_a_watched_window_is_reported_until_it_is_released(monkeypatch):
    _fake(monkeypatch)
    places = iter([{"frame": [1, 2, 3, 4], "occluders": []}] * 2 + [{"frame": [5, 6, 3, 4], "occluders": []}] * 100)
    monkeypatch.setattr(window_watch, "window_place", lambda window_id: next(places))
    monkeypatch.setattr(window_watch, "REPORT_SECONDS", 0.01)
    reports, moved = [], threading.Event()

    def report(info, payload):
        reports.append((info, payload))
        if len(reports) >= 2:
            moved.set()

    watch = window_watch.WindowWatch(report=report)
    watch.touch("s", 7, {"app_name": "Calculator", "buttons": [8, 6, 52, 14], "owner": ("conv", "run")})
    assert moved.wait(30)
    watch.release("s")
    _settle()
    frames = [payload["frame"] for _, payload in reports]
    assert frames[:2] == [[1, 2, 3, 4], [5, 6, 3, 4]], "only changes are reported"
    info, last = reports[-1]
    assert last["active"] is False and info["owner"] == ("conv", "run")
    assert all(payload["app_name"] == "Calculator" and payload["buttons"] == [8, 6, 52, 14] for _, payload in reports)
