"""A stopped ``--print`` run waits the same way on both transports.

One table of stops, each run on the in-process run (``print_turn._stop_run``)
and on the relay of a turn the Worker runs (``worker_turn._Relay.stop``):
both read the grace periods and the second-signal rule from ``print_stop``.
"""
from __future__ import annotations

import json
import signal
import threading
import time
import types

import pytest

from openprogram.interfaces.cli import print_stop, print_turn, worker_turn


@pytest.fixture(autouse=True)
def _signals_restored(monkeypatch):
    saved = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    monkeypatch.setattr(print_stop, "_held", False)
    monkeypatch.setattr(print_stop, "_stop_signal", None)
    monkeypatch.setattr(print_stop, "_console_closed_at", None)
    print_stop._noted.clear()
    yield
    for number, handler in saved.items():
        signal.signal(number, handler)
    print_stop._noted.clear()


# (id, grace, second-signal grace, the turn stops this long after the
#  cancel (None: never), a further signal this long after it (None: none),
#  stopped?, the stop took at least, and less than)
SCENARIOS = [
    ("stops-in-time", 5.0, 2.0, 0.2, None, True, 0.15, 1.5),
    ("grace-runs-out", 0.4, 2.0, None, None, False, 0.35, 1.5),
    ("second-signal-shortens-the-wait", 30.0, 0.3, None, 0.05, False, 0.25, 1.5),
    ("second-signal-still-waits-for-a-turn-that-stops", 30.0, 1.0, 0.4, 0.05, True, 0.35, 1.5),
]


def _later(seconds, action):
    if seconds is not None:
        threading.Timer(seconds, action).start()


def _in_process(monkeypatch, stops_after, signal_after) -> bool:
    from openprogram.runtime.execution import run_control

    finished = threading.Event()

    def cancel(_execution_id):
        _later(stops_after, finished.set)
        _later(signal_after, print_stop._noted.set)

    monkeypatch.setattr(run_control, "cancel_execution", cancel)

    class _Adapter:
        def fail_admission(self, *_args, **_kwargs):
            pass

    run = print_turn.PrintRun(session_id="s")
    print_turn._stop_run(_Adapter(), types.SimpleNamespace(execution_id="e"), finished, {}, run)
    return not run.left_running


class _Worker:
    """A socket to a Worker that ends the execution ``stops_after`` seconds
    after the cancel, or never."""

    def __init__(self, stops_after, signal_after, noted):
        self.stops_after = stops_after
        self.signal_after = signal_after
        self.noted = noted
        self.cancelled_at = None
        self.sent: list[dict] = []

    def send(self, raw):
        self.sent.append(json.loads(raw))
        if self.cancelled_at is None:
            self.cancelled_at = time.monotonic()
            _later(self.signal_after, self.noted.set)

    def recv(self, timeout=None):
        if (self.stops_after is not None and self.cancelled_at is not None
                and time.monotonic() - self.cancelled_at >= self.stops_after):
            self.stops_after = None
            return json.dumps({"type": "execution.updated", "execution": {
                "execution_id": "exec1", "status": "cancelled", "status_version": 9}})
        time.sleep(min(timeout or 0.01, 0.01))
        raise TimeoutError


def _on_worker(_monkeypatch, stops_after, signal_after) -> bool:
    noted = threading.Event()
    relay = worker_turn._Relay("s", "rid", None, None, on_stop=lambda: noted)
    relay.execution_id, relay.status, relay.status_version = "exec1", "running", 1
    stopped = relay.stop(_Worker(stops_after, signal_after, noted))
    return stopped.confirmed


@pytest.mark.parametrize("transport", [_in_process, _on_worker], ids=["in-process", "worker"])
@pytest.mark.parametrize("name, grace, second, stops_after, signal_after, stopped, least, most",
                         SCENARIOS, ids=[row[0] for row in SCENARIOS])
def test_a_stop_waits_by_one_rule_on_both_transports(monkeypatch, transport, name, grace, second,
                                                     stops_after, signal_after, stopped,
                                                     least, most):
    monkeypatch.setattr(print_stop, "CANCEL_GRACE_S", grace)
    monkeypatch.setattr(print_stop, "SECOND_SIGNAL_GRACE_S", second)

    began = time.monotonic()
    assert transport(monkeypatch, stops_after, signal_after) is stopped
    took = time.monotonic() - began
    assert least <= took < most


def test_the_clock_counts_the_short_wait_from_the_cancel():
    noted = threading.Event()
    clock = print_stop.StopClock(noted)
    assert clock.remaining() == pytest.approx(print_stop.CANCEL_GRACE_S, abs=0.05)
    time.sleep(0.1)
    clock.sent()
    noted.set()
    assert clock.remaining() == pytest.approx(print_stop.SECOND_SIGNAL_GRACE_S, abs=0.05)


def test_neither_transport_keeps_its_own_stop_constants():
    for module in (print_turn, worker_turn):
        for name in ("CANCEL_GRACE_S", "SECOND_SIGNAL_GRACE_S", "_STOP_WAIT_S",
                     "_STOP_WAIT_AFTER_SECOND_SIGNAL_S", "_STALE_RETRIES", "_STOP_POLL_S"):
            assert not hasattr(module, name), (module.__name__, name)


def test_each_signal_stops_with_128_plus_its_number():
    assert [print_stop.stopped_exit_code(name) for name in ("SIGINT", "SIGTERM", "SIGBREAK", None)] == [
        130, 143, 143, print_stop.STOPPED_EXIT_CODE]


# Windows: closing the console stops the run the way a signal does, with the
# few seconds Windows leaves before it ends the process (Node maps the close
# to SIGHUP, which is how Claude Code's --print stops on it).

def test_closing_the_console_stops_the_run_and_shortens_its_wait():
    interrupted: list[bool] = []
    clock = print_stop.StopClock()

    handled = print_stop._console_closed(print_stop._CTRL_CLOSE_EVENT,
                                         interrupt_main=lambda: interrupted.append(True), hold_s=0)

    assert handled and interrupted == [True]
    assert print_stop.stop_signal() == "SIGHUP"
    assert print_stop.stopped_exit_code(print_stop.stop_signal()) == 129
    assert clock.remaining() <= print_stop.CONSOLE_CLOSE_GRACE_S


def test_a_console_close_while_stopping_is_noted_not_raised():
    print_stop.hold_signals()
    interrupted: list[bool] = []

    assert print_stop._console_closed(print_stop._CTRL_CLOSE_EVENT,
                                      interrupt_main=lambda: interrupted.append(True), hold_s=0)

    assert interrupted == [] and print_stop._noted.is_set()


def test_ctrl_c_and_ctrl_break_are_left_to_the_signal_handlers():
    assert print_stop._console_closed(0, interrupt_main=lambda: None, hold_s=0) is False
    assert print_stop._console_closed(1, interrupt_main=lambda: None, hold_s=0) is False


def test_a_quiet_worker_socket_is_read_in_bounded_waits():
    # On Windows a stop signal runs its handler only once a blocking read
    # returns; an unbounded read of a quiet socket would hold the stop.
    waits: list[float | None] = []

    class Quiet:
        def recv(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 3:
                raise LookupError("enough")
            raise TimeoutError

    relay = worker_turn._Relay("s", "rid", None, None)
    with pytest.raises(LookupError):
        relay._follow(Quiet())
    assert all(w is not None and w <= worker_turn._SIGNAL_POLL_S for w in waits)


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="CTRL_BREAK_EVENT exists only on Windows")
def test_ctrl_break_stops_a_child_with_143():
    import subprocess
    import sys

    # Windows wakes a blocking sleep or wait for SIGINT only; a SIGBREAK
    # handler runs once the wait returns. The child therefore waits the way
    # a --print run does, in short polls (print_turn, worker_turn).
    child = subprocess.Popen(
        [sys.executable, "-c", (
            "import threading\n"
            "from openprogram.interfaces.cli import print_stop\n"
            "finished = threading.Event()\n"
            "try:\n"
            "    with print_stop.interrupt_on_terminate():\n"
            "        print('ready', flush=True)\n"
            "        for _ in range(150):\n"
            "            finished.wait(0.2)\n"
            "except KeyboardInterrupt:\n"
            "    raise SystemExit(print_stop.stopped_exit_code(print_stop.stop_signal()))\n"
        )],
        stdout=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    assert child.stdout.readline().strip() == "ready"
    child.send_signal(signal.CTRL_BREAK_EVENT)
    assert child.wait(timeout=20) == 143
