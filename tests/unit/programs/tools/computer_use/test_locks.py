"""Who may operate a screen now: shared and exclusive keys, within one thread.

Threads and processes contending for one key are in
tests/component/programs/tools/test_computer_use_locks.py.
"""
from __future__ import annotations

from collections import deque
import functools
import os
from pathlib import Path

import pytest

from openprogram.programs.tools.computer_use.computer_use.routing import dispatch, locks
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.routing.locks import Busy, Locks, held

from tests.support.screen_targets import FakeProvider

MACHINE = locks.machine_key("lab")
APPLICATION = locks.application_key("local", "Calculator")
TAB = locks.tab_key("local", "built_in", "t1")
SHORT = 0.15


@pytest.fixture(autouse=True)
def _own_lock_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(locks, "_root", lambda: tmp_path / "locks")


def _free(key: str, exclusive: bool = True) -> bool:
    try:
        with held(Locks(exclusive=(key,)) if exclusive else Locks(shared=(key,)), seconds=SHORT):
            return True
    except Busy:
        return False


def test_keys_name_the_machine_instance_the_application_and_the_tab():
    assert MACHINE == "machine/lab"
    assert locks.machine_key("builder/conversation-1") == "machine/builder/conversation-1"
    assert APPLICATION == "application/local/Calculator"
    assert TAB == "tab/local/built_in/t1"
    assert not Locks() and Locks(shared=(MACHINE,)) and Locks(exclusive=(MACHINE,))


@pytest.mark.parametrize("key", [MACHINE, APPLICATION, TAB])
def test_shared_holders_hold_together(key):
    with held(Locks(shared=(key,))):
        assert _free(key, exclusive=False)
        assert not _free(key)


@pytest.mark.parametrize("key", [MACHINE, APPLICATION, TAB])
def test_an_exclusive_holder_keeps_everyone_out(key):
    with held(Locks(exclusive=(key,))):
        assert not _free(key)
        assert not _free(key, exclusive=False)
    assert _free(key)


def test_background_steps_on_two_applications_go_together_and_the_pointer_waits_for_both():
    # app: steps delivered in the background share the machine and take the application.
    calculator = Locks(shared=(locks.machine_key("local"),), exclusive=(locks.application_key("local", "Calculator"),))
    notes = Locks(shared=(locks.machine_key("local"),), exclusive=(locks.application_key("local", "Notes"),))
    with held(calculator):
        with held(notes, seconds=SHORT):
            pass
        with pytest.raises(Busy):
            with held(calculator, seconds=SHORT):
                pass
        # A step that needs the real pointer takes the machine.
        assert not _free(locks.machine_key("local"))


def test_busy_names_the_key_and_the_wait():
    with held(Locks(exclusive=(MACHINE,))):
        with pytest.raises(Busy) as caught:
            with held(Locks(exclusive=(MACHINE,)), seconds=SHORT):
                pass
    assert caught.value.key == MACHINE and caught.value.seconds == SHORT
    assert locks.WAIT_SECONDS == 60


def test_a_lock_is_given_back_when_the_call_raises():
    with pytest.raises(RuntimeError):
        with held(Locks(shared=(MACHINE,), exclusive=(APPLICATION,))):
            raise RuntimeError("the step failed")
    assert _free(MACHINE) and _free(APPLICATION)


def test_a_cancelled_call_stops_waiting_and_holds_nothing():
    class Cancelled(Exception):
        pass

    asked = []

    def cancelled():
        asked.append(True)
        raise Cancelled()

    with held(Locks(exclusive=(MACHINE,))):
        with pytest.raises(Cancelled):
            with held(Locks(shared=(APPLICATION,), exclusive=(MACHINE,)), cancelled=cancelled, seconds=30):
                pass
        assert asked
    assert _free(MACHINE) and _free(APPLICATION)


def test_a_call_that_waited_too_long_answers_screen_busy_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(dispatch.locks, "held", functools.partial(locks.held, seconds=SHORT))
    provider = FakeProvider("screen", "lab", locks=lambda subject, steps: Locks(exclusive=(MACHINE,)))
    with locks.held(Locks(exclusive=(MACHINE,))):
        result = dispatch.run_steps(provider, {}, [("click", {"x": 1, "y": 2})], Call("c", "a"))
    assert result.is_error and result.text.startswith("SCREEN_BUSY: another agent has been operating device=\"lab\", type=\"screen\" for ")
    assert result.json_data == {"action_dispatched": False}
    assert provider.calls == []


def test_a_step_holds_its_providers_locks_while_it_runs():
    seen = []

    def act(subject, steps, call):
        seen.append(_free(MACHINE))
        from openprogram.runtime.agent.tool_execution import ToolReturn
        return ToolReturn(text="Done")

    provider = FakeProvider("screen", "lab", locks=lambda subject, steps: Locks(exclusive=(MACHINE,)),
                            act=act)
    dispatch.run_steps(provider, {}, [("click", {"x": 1, "y": 2})], Call("c", "a"))
    assert seen == [False] and _free(MACHINE)


def test_the_wait_bounds_the_whole_call_not_each_key(monkeypatch):
    deadlines = []

    def acquire(key, exclusive, wait):
        deadlines.append(wait.deadline)
        return lambda: None

    monkeypatch.setattr(locks, "_acquire", acquire)
    with held(Locks(shared=(MACHINE,), exclusive=(APPLICATION, TAB))):
        pass
    assert len(deadlines) == 3 and len(set(deadlines)) == 1


def test_a_shared_holder_gives_its_lock_back_before_removing_its_file(monkeypatch):
    # Windows removes no file that is still open: the reader file is unlocked and closed first.
    done = []
    release, unlink = locks._release, Path.unlink

    def released(fd):
        done.append("release")
        release(fd)

    def removed(path, *args, **kwargs):
        if path.name.startswith("r-"):
            done.append("unlink")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(locks, "_release", released)
    monkeypatch.setattr(Path, "unlink", removed)
    with held(Locks(shared=(MACHINE,))):
        done.clear()
    assert done == ["release", "unlink"]


@pytest.mark.parametrize("stop", [TimeoutError("GUI operation deadline exceeded"),
                                  PermissionError("GUI operation cancelled or revoked")])
def test_a_step_stopped_while_it_waits_for_a_lock_says_nothing_was_sent(stop):
    # Code's broker stops a step with a plain error; in the wait, no input has left, and the answer says so.
    def check():
        raise stop

    provider = FakeProvider("screen", "lab", locks=lambda subject, steps: Locks(exclusive=(MACHINE,)))
    with locks.held(Locks(exclusive=(MACHINE,))):
        result = dispatch.run_steps(provider, {}, [("click", {"x": 1, "y": 2})], Call("c", "a", check_cancelled=check))
    assert result.is_error and result.text.startswith("TIMEOUT: stopped while waiting for device=\"lab\", type=\"screen\": ")
    assert result.json_data == {"action_dispatched": False}
    assert provider.calls == []


def test_a_stop_of_the_turn_while_waiting_still_stops_it():
    class Stopped(BaseException):
        pass

    def check():
        raise Stopped()

    provider = FakeProvider("screen", "lab", locks=lambda subject, steps: Locks(exclusive=(MACHINE,)))
    with locks.held(Locks(exclusive=(MACHINE,))):
        with pytest.raises(Stopped):
            dispatch.run_steps(provider, {}, [("click", {"x": 1, "y": 2})], Call("c", "a", check_cancelled=check))


def _waiting_elsewhere(name: str) -> int:
    """A ticket another process holds in MACHINE's queue: a second open file locks apart from this one's."""
    path = locks._folder(MACHINE) / name
    fd = locks._open(path)
    assert locks._try(fd)
    return fd


def test_a_request_from_another_process_that_came_first_goes_first():
    earlier = _waiting_elsewhere(f"q-{0:020d}-1-1")
    try:
        # The key is free, but an earlier request still waits for it.
        assert not _free(MACHINE) and not _free(MACHINE, exclusive=False)
    finally:
        locks._release(earlier)
    # Its process is gone: its ticket is removed and the key is taken.
    assert _free(MACHINE)
    assert list(locks._folder(MACHINE).glob("q-*")) == []


def test_a_request_from_another_process_that_came_later_waits_behind():
    later = _waiting_elsewhere(f"q-{10 ** 19 * 9:020d}-1-1")
    try:
        assert _free(MACHINE)
    finally:
        locks._release(later)


def test_a_request_leaves_the_queue_when_it_gives_up_or_holds_the_key():
    with held(Locks(exclusive=(MACHINE,))):
        assert list(locks._folder(MACHINE).glob("q-*")) == []
        assert not _free(MACHINE)
    assert list(locks._folder(MACHINE).glob("q-*")) == []


def test_a_request_waiting_behind_another_of_its_process_already_holds_its_place():
    # Its ticket is taken on arrival, so requests of other processes that come later stay behind it.
    class Stop(Exception):
        pass

    seen: list[list[str]] = []

    def cancelled():
        seen.append([path.name for path in locks._folder(MACHINE).glob("q-*")])
        raise Stop()

    locks._queues[MACHINE] = deque([0])
    try:
        with pytest.raises(Stop):
            with held(Locks(exclusive=(MACHINE,)), cancelled=cancelled):
                pass
    finally:
        locks._queues.pop(MACHINE, None)
    assert len(seen[0]) == 1 and seen[0][0].split("-")[2] == str(os.getpid())
    assert list(locks._folder(MACHINE).glob("q-*")) == []


def test_an_earlier_ticket_of_its_own_process_is_ordered_by_its_turn_not_its_ticket():
    # The in-process queue already put that request ahead; waiting for its ticket as well could wait forever.
    own = _waiting_elsewhere(f"q-{0:020d}-{os.getpid()}-1")
    try:
        assert _free(MACHINE)
    finally:
        locks._release(own)
