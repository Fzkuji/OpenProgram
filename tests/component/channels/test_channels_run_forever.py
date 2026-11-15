"""Channel.run_forever — crash auto-reconnect with exponential backoff.

Crashing run() gets restarted (backoff doubling to a cap, reset after a
healthy stretch); a clean return is a permanent stop; stop event exits
promptly, including mid-backoff.
"""
from __future__ import annotations

import threading

from openprogram.interfaces.channels import _heartbeats
from openprogram.interfaces.channels.base import Channel


class _Adapter(Channel):
    platform_id = "faketg"
    RESTART_BACKOFF = 0.01
    RESTART_BACKOFF_MAX = 0.04

    def __init__(self, behaviors) -> None:
        super().__init__(account_id="a1")
        self.behaviors = list(behaviors)   # each: "crash" | "clean"
        self.runs = 0

    def run(self, stop: threading.Event) -> None:
        self.runs += 1
        action = self.behaviors.pop(0) if self.behaviors else "clean"
        if action == "crash":
            raise RuntimeError("gateway dropped")
        # "clean": permanent stop (e.g. credentials invalid)


def test_crash_restarts_then_clean_return_stops() -> None:
    ch = _Adapter(["crash", "crash", "clean"])
    stop = threading.Event()
    ch.run_forever(stop)          # returns without hanging
    assert ch.runs == 3


def test_clean_exit_never_restarts() -> None:
    ch = _Adapter(["clean"])
    ch.run_forever(threading.Event())
    assert ch.runs == 1


def test_stop_event_wins_during_backoff() -> None:
    class _CrashForever(_Adapter):
        RESTART_BACKOFF = 30.0    # long enough that only stop can end it

        def run(self, stop: threading.Event) -> None:
            self.runs += 1
            raise RuntimeError("down")

    ch = _CrashForever([])
    stop = threading.Event()
    t = threading.Thread(target=ch.run_forever, args=(stop,), daemon=True)
    t.start()
    stop.set()
    t.join(timeout=30)
    assert not t.is_alive()
    assert ch.runs >= 1


def test_backoff_doubles_and_caps(monkeypatch) -> None:
    waits: list[float] = []

    ch = _Adapter(["crash"] * 5 + ["clean"])
    stop = threading.Event()
    real_wait = stop.wait

    def spy_wait(timeout=None):
        waits.append(timeout)
        return real_wait(0)       # don't actually sleep

    monkeypatch.setattr(stop, "wait", spy_wait)
    # freeze time so the 60s "healthy run" reset never fires
    import openprogram.interfaces.channels.base as base_mod
    monkeypatch.setattr(base_mod.time, "time", lambda: 1000.0)

    ch.run_forever(stop)
    assert waits == [0.01, 0.02, 0.04, 0.04, 0.04]   # doubled, then capped


def test_worker_runs_adapters_via_run_forever() -> None:
    """The worker entry (_safe_run_channel) must call run_forever, not
    bare run — otherwise the reconnect loop is dead code."""
    import inspect
    from openprogram.operations.worker import runner
    src = inspect.getsource(runner._safe_run_channel)
    assert "run_forever(" in src


def test_a_crash_marks_the_adapter_as_reconnecting() -> None:
    _heartbeats.clear_stop("faketg", "a1")
    _Adapter(["crash", "clean"]).run_forever(threading.Event())
    assert _heartbeats.is_retrying("faketg", "a1")
    _heartbeats.clear_stop("faketg", "a1")
    assert not _heartbeats.is_retrying("faketg", "a1")


def test_a_clean_run_does_not_mark_reconnecting() -> None:
    _heartbeats.clear_stop("faketg", "a1")
    _Adapter(["clean"]).run_forever(threading.Event())
    assert not _heartbeats.is_retrying("faketg", "a1")
