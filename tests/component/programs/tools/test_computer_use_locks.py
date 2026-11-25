"""Locks on a screen hold across threads and across the worker's processes, first come first served."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time

import pytest

from openprogram.programs.tools.computer_use.computer_use.routing import locks
from openprogram.programs.tools.computer_use.computer_use.routing.locks import Busy, Locks, held

KEY = locks.machine_key("lab")
SHORT = 0.3


@pytest.fixture
def folder(monkeypatch, tmp_path):
    root = tmp_path / "locks"
    monkeypatch.setattr(locks, "_root", lambda: root)
    return root


def _free(key: str, exclusive: bool = True) -> bool:
    try:
        with held(Locks(exclusive=(key,)) if exclusive else Locks(shared=(key,)), seconds=SHORT):
            return True
    except Busy:
        return False


class _Holder(threading.Thread):
    """One call on its own thread: takes its locks, says so, holds them until released."""

    def __init__(self, name: str, wanted: Locks, order: list[str], seconds: float = 20):
        super().__init__(name=name, daemon=True)
        self.wanted, self.order, self.seconds = wanted, order, seconds
        self.holding, self.release = threading.Event(), threading.Event()
        self.error: BaseException | None = None

    def run(self):
        try:
            with held(self.wanted, seconds=self.seconds):
                self.order.append(self.name)
                self.holding.set()
                self.release.wait(20)
        except BaseException as exc:
            self.error = exc
            self.holding.set()


def _queued(count: int, timeout: float = 5) -> bool:
    """Whether ``count`` requests of this process have taken their place in the queue for KEY."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with locks._queues_guard:
            if len(locks._queues.get(KEY, ())) >= count:
                return True
        time.sleep(0.01)
    return False


def _waiting(thread: _Holder, settle: float = 0.3) -> bool:
    """Whether a started call is still waiting for its locks."""
    return not thread.holding.wait(settle)


def test_an_exclusive_holder_on_one_thread_keeps_another_thread_waiting(folder):
    order: list[str] = []
    first = _Holder("first", Locks(exclusive=(KEY,)), order)
    second = _Holder("second", Locks(exclusive=(KEY,)), order)
    first.start()
    assert first.holding.wait(5)
    second.start()
    assert _waiting(second)
    first.release.set()
    assert second.holding.wait(5)
    second.release.set()
    for thread in (first, second):
        thread.join(5)
        assert thread.error is None
    assert order == ["first", "second"]


def test_a_waiting_exclusive_request_goes_before_later_shared_ones(folder):
    order: list[str] = []
    reader = _Holder("reader", Locks(shared=(KEY,)), order)
    writer = _Holder("writer", Locks(exclusive=(KEY,)), order)
    late = _Holder("late reader", Locks(shared=(KEY,)), order)
    reader.start()
    assert reader.holding.wait(5)
    writer.start()
    assert _queued(1) and _waiting(writer)
    late.start()
    # Shared with the reader, but it came after the writer: it waits for the writer's turn.
    assert _waiting(late)
    reader.release.set()
    assert writer.holding.wait(5)
    assert _waiting(late)
    writer.release.set()
    assert late.holding.wait(5)
    late.release.set()
    for thread in (reader, writer, late):
        thread.join(5)
        assert thread.error is None
    assert order == ["reader", "writer", "late reader"]


def test_requests_take_their_turn_in_the_order_they_came(folder):
    order: list[str] = []
    holder = _Holder("holder", Locks(exclusive=(KEY,)), order)
    holder.start()
    assert holder.holding.wait(5)
    waiting = [_Holder(f"call {number}", Locks(exclusive=(KEY,)), order) for number in range(1, 4)]
    # The holder has left the queue; each call starts once the one before it has its place.
    for queued, thread in enumerate(waiting, start=1):
        thread.start()
        assert _queued(queued)
        assert _waiting(thread, 0.15)
    holder.release.set()
    for thread in waiting:
        assert thread.holding.wait(5)
        thread.release.set()
        thread.join(5)
        assert thread.error is None
    assert order == ["holder", "call 1", "call 2", "call 3"]


def test_a_call_that_waits_past_its_time_is_busy_and_the_holder_keeps_its_lock(folder):
    order: list[str] = []
    holder = _Holder("holder", Locks(exclusive=(KEY,)), order)
    waiter = _Holder("waiter", Locks(shared=(KEY,)), order, seconds=SHORT)
    holder.start()
    assert holder.holding.wait(5)
    waiter.start()
    waiter.join(5)
    assert isinstance(waiter.error, Busy) and order == ["holder"]
    assert not _free(KEY)
    holder.release.set()
    holder.join(5)
    assert _free(KEY)


def test_a_call_cancelled_while_waiting_leaves_the_queue(folder):
    class Cancelled(Exception):
        pass

    stop = threading.Event()
    outcome: list[BaseException] = []

    def cancelled():
        if stop.is_set():
            raise Cancelled()

    def wait_for_the_machine():
        try:
            with held(Locks(exclusive=(KEY,)), cancelled=cancelled, seconds=20):
                pass
        except BaseException as exc:
            outcome.append(exc)

    order: list[str] = []
    holder = _Holder("holder", Locks(exclusive=(KEY,)), order)
    holder.start()
    assert holder.holding.wait(5)
    waiter = threading.Thread(target=wait_for_the_machine, daemon=True)
    waiter.start()
    time.sleep(0.2)
    stop.set()
    waiter.join(5)
    assert len(outcome) == 1 and isinstance(outcome[0], Cancelled)
    holder.release.set()
    holder.join(5)
    # The cancelled request is not left at the head of the queue.
    assert _free(KEY)


_CHILD = textwrap.dedent("""
    import sys
    from pathlib import Path
    from openprogram.programs.tools.computer_use.computer_use.routing import locks
    locks._root = lambda: Path(sys.argv[1])
    wanted = locks.Locks(shared=(sys.argv[2],)) if sys.argv[3] == "shared" else locks.Locks(exclusive=(sys.argv[2],))
    print("asking", flush=True)
    with locks.held(wanted, seconds=20):
        print("holding", flush=True)
        sys.stdin.readline()
    print("released", flush=True)
""")


def _child(folder, mode: str) -> subprocess.Popen:
    child = subprocess.Popen([sys.executable, "-c", _CHILD, str(folder), KEY, mode], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "asking"
    assert child.stdout.readline().strip() == "holding"
    return child


def test_a_lock_held_by_another_process_keeps_this_one_out(folder):
    child = _child(folder, "exclusive")
    try:
        assert not _free(KEY)
        assert not _free(KEY, exclusive=False)
        child.stdin.write("\n")
        child.stdin.flush()
        assert child.stdout.readline().strip() == "released"
    finally:
        child.wait(10)
    assert _free(KEY) and _free(KEY, exclusive=False)


def test_a_shared_lock_of_another_process_admits_shared_holders_only(folder):
    child = _child(folder, "shared")
    try:
        assert _free(KEY, exclusive=False)
        assert not _free(KEY)
    finally:
        child.stdin.write("\n")
        child.stdin.flush()
        child.wait(10)
    assert _free(KEY)


def test_a_later_exclusive_request_of_another_process_waits_behind_an_earlier_shared_one(folder):
    first = _child(folder, "exclusive")
    order: list[str] = []
    reader = _Holder("reader", Locks(shared=(KEY,)), order)
    reader.start()
    later = None
    try:
        assert _queued(1) and _waiting(reader)
        later = subprocess.Popen([sys.executable, "-c", _CHILD, str(folder), KEY, "exclusive"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert later.stdout.readline().strip() == "asking"
        time.sleep(0.5)
        first.stdin.write("\n")
        first.stdin.flush()
        assert reader.holding.wait(5) and reader.error is None
        reader.release.set()
        reader.join(5)
        assert later.stdout.readline().strip() == "holding"
    finally:
        reader.release.set()
        for child in (first, later):
            if child is not None:
                try:
                    child.stdin.write("\n")
                    child.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass
                child.wait(10)
    assert order == ["reader"]


def test_a_process_that_dies_gives_its_locks_back(folder):
    child = _child(folder, "exclusive")
    child.kill()
    child.wait(10)
    assert _free(KEY)


_LOGGED = _CHILD.replace('print("holding", flush=True)',
                         'open(sys.argv[4], "a").write(sys.argv[5] + "\\n")\n    print("holding", flush=True)')


def _logged(folder, mode: str, log, name: str) -> subprocess.Popen:
    child = subprocess.Popen([sys.executable, "-c", _LOGGED, str(folder), KEY, mode, str(log), name],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "asking"
    # Its place in the queue is taken just after it says so.
    time.sleep(0.3)
    return child


def _let_go(child: subprocess.Popen) -> None:
    try:
        child.stdin.write("\n")
        child.stdin.flush()
    except (BrokenPipeError, ValueError):
        pass


def test_waiting_requests_of_other_processes_go_in_the_order_they_came(folder, tmp_path):
    log = tmp_path / "order"
    log.write_text("")
    first = _child(folder, "exclusive")
    order: list[str] = []
    reader = _Holder("reader", Locks(shared=(KEY,)), order)
    reader.start()
    writer = late = None
    try:
        assert _queued(1) and _waiting(reader)
        writer = _logged(folder, "exclusive", log, "writer")
        late = _logged(folder, "shared", log, "late reader")
        _let_go(first)
        assert reader.holding.wait(5) and reader.error is None
        # The later shared request would fit beside the reader, but the exclusive one came first.
        time.sleep(0.5)
        assert log.read_text() == ""
        reader.release.set()
        reader.join(5)
        assert writer.stdout.readline().strip() == "holding"
        _let_go(writer)
        assert late.stdout.readline().strip() == "holding"
    finally:
        reader.release.set()
        for child in (first, writer, late):
            if child is not None:
                _let_go(child)
                child.wait(10)
    assert log.read_text().splitlines() == ["writer", "late reader"]
