"""A Worker asked for a sample writes where its threads were working."""
from __future__ import annotations

import pytest

import sys
import threading

from openprogram.interfaces.server.server_runtime import thread_sample


def test_a_request_file_is_answered_once_with_the_sample(tmp_path):
    requests = thread_sample.SampleRequests(tmp_path, sample=lambda: "busy there\n")
    assert requests.check() is False, "nothing was asked for"
    (tmp_path / thread_sample.REQUEST_NAME).write_text("", encoding="utf-8")
    assert requests.check() is True
    assert (tmp_path / thread_sample.RESULT_NAME).read_text(encoding="utf-8") == "busy there\n"
    assert not (tmp_path / thread_sample.REQUEST_NAME).exists()
    assert requests.check() is False, "one request is one sample"


def test_a_sample_lists_working_threads_by_share_and_leaves_waiting_ones_out():
    done, started = [], threading.Event()

    def openprogram_fold():
        started.set()
        while not done:
            pass

    waiting = threading.Event()
    threads = [threading.Thread(target=openprogram_fold), threading.Thread(target=waiting.wait)]
    for thread in threads:
        thread.start()
    started.wait()
    try:
        # This test file is not product code, so every frame counts as the product's here.
        original = thread_sample._own_calls
        thread_sample._own_calls = lambda frame: frame.f_code.co_name
        clock = iter(range(100))
        # Only this test's threads: ones an earlier test left in the process are not its subject.
        ours = {thread.ident for thread in threads}
        report = thread_sample.take_sample(
            seconds=5, interval=0.02, sleep=lambda seconds: None, clock=lambda: next(clock),
            frames=lambda: {key: frame for key, frame in sys._current_frames().items() if key in ours})
    finally:
        thread_sample._own_calls = original
        done.append(True)
        waiting.set()
        for thread in threads:
            thread.join()
    lines = report.splitlines()
    assert lines[0].startswith("5 looks at every thread over 5 s"), "the clock ends it, not a count"
    assert lines[1] == "100.0%  openprogram_fold"
    assert "wait" not in report.split("\n", 1)[1]


def test_a_sample_of_idle_threads_says_so():
    report = thread_sample.take_sample(seconds=0.02, interval=0.02, frames=dict, sleep=lambda seconds: None)
    assert report.endswith("no thread was working in any look\n")


def test_a_sample_says_which_threads_used_the_processor():
    looks = iter(range(100))
    # Thread 1 works through the sample; thread 2 starts during it; thread 3 only waits.
    used = {1: iter([5.0, 5.5, 6.0]), 2: iter([0.25, 0.5]), 3: iter([9.0, 9.0, 9.0])}
    seen = iter([{1: None, 3: None}, {1: None, 2: None, 3: None}, {1: None, 2: None, 3: None}])

    class Frame:
        f_back = None

        class f_code:
            co_name = "fold"
            co_filename = "/x/openprogram/store.py"

    report = thread_sample.take_sample(
        seconds=3, interval=0.02, sleep=lambda seconds: None, clock=lambda: next(looks),
        frames=lambda: {ident: Frame for ident in next(seen)},
        cpu=lambda ident: next(used[ident]),
        names=lambda: {1: "reader", 2: "late", 3: "waiter"})
    tail = report.split("\n\n")[1].splitlines()
    assert tail[0].startswith("Processor time by thread, 1.50 s in all")
    assert tail[1:] == ["  1.00 s  reader  store.py:fold", "  0.50 s  late  store.py:fold"]


@pytest.mark.slow
def test_the_request_file_event_starts_the_sample_without_a_timer(tmp_path, monkeypatch):
    """The folder is not read every few seconds: the request's file event
    wakes the watcher."""
    import time

    from openprogram.host._platform import changes

    monkeypatch.setattr(thread_sample, "POLL_SECONDS", 600.0)
    changes.start_watching()
    requests = thread_sample.SampleRequests(tmp_path, sample=lambda: "busy there\n")
    try:
        requests.start()
        time.sleep(1.5)  # the watcher has started on the folder
        (tmp_path / thread_sample.REQUEST_NAME).write_text("", encoding="utf-8")
        result = tmp_path / thread_sample.RESULT_NAME
        for _ in range(100):
            if result.exists():
                break
            time.sleep(0.05)
        assert result.read_text(encoding="utf-8") == "busy there\n"
    finally:
        requests.stop()
        changes.stop_watching()
        changes.unwatch(tmp_path, requests.topic)
