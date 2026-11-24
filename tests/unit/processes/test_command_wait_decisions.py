"""What a command's supervisor and group leader wait for, decided without a
real process: nothing in them wakes on a tick."""
from __future__ import annotations

from openprogram.host import cancellation
from openprogram.host.processes import group_guard
from openprogram.host.processes.supervisor import STOP_GRACE_SECONDS, wait_timeout


def test_the_supervisor_sleeps_until_woken_except_for_a_stops_grace():
    assert wait_timeout(None, False, 100.0) is None
    assert wait_timeout(100.0, False, 101.5) == STOP_GRACE_SECONDS - 1.5
    assert wait_timeout(100.0, False, 200.0) == 0.0
    # Killed: only the exit it causes wakes the loop.
    assert wait_timeout(100.0, True, 200.0) is None


class _Group:
    """Members listed in turns; ``hold`` says which exit on each wait."""

    def __init__(self, listings, exits):
        self.listings = list(listings)
        self.exits = list(exits)
        self.held = []
        self.closed = 0

    def members(self):
        return self.listings.pop(0) if self.listings else []

    def hold(self, pids):
        self.held.append(list(pids))
        exits = self.exits

        def wait(timeout):
            return exits.pop(0) if exits else set()

        def close():
            self.closed += 1

        wait.close = close
        return wait, set(pids)


def test_the_leader_waits_on_exits_then_lists_the_group_again():
    # 11 starts 12 before it ends; the second listing finds 12.
    group = _Group(listings=[[11], [11], [12], [12], []], exits=[{11}, {12}])
    assert group_guard._wait_until_alone(members=group.members, hold=group.hold) is True
    assert group.held == [[11], [12]]
    assert group.closed == 2


def test_a_member_gone_before_it_was_held_is_not_waited_on():
    # 21 ended and its pid could be reused: the listing after holding drops it.
    group = _Group(listings=[[21, 22], [22], []], exits=[{22}])
    assert group_guard._wait_until_alone(members=group.members, hold=group.hold) is True
    assert group.exits == []


def test_a_deadline_ends_the_wait_with_members_left(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(group_guard.time, "monotonic", lambda: now[0])
    group = _Group(listings=[[31], [31]], exits=[])

    def hold(pids):
        wait, held = group.hold(pids)

        def timed(timeout):
            assert timeout == 2.0
            now[0] += timeout
            return set()

        timed.close = wait.close
        return timed, held

    assert group_guard._wait_until_alone(deadline=2.0, members=group.members, hold=hold) is False


def test_without_exit_events_the_group_is_listed_again_after_a_short_look(monkeypatch):
    looks = []
    monkeypatch.setattr(group_guard.time, "sleep", looks.append)
    listings = [[41], []]
    assert group_guard._wait_until_alone(members=lambda: listings.pop(0),
                                         hold=lambda pids: (None, set(pids))) is True
    assert looks == [group_guard.FALLBACK_LOOK_SECONDS]


def test_without_a_host_waker_on_cancel_registers_nothing(monkeypatch):
    monkeypatch.setattr(cancellation, "_cancellation_waker", None)
    called = []
    cancellation.on_cancel(lambda: called.append(1))()
    assert called == []


def test_a_host_waker_receives_the_callback_and_its_undo(monkeypatch):
    registered, undone = [], []

    def waker(callback):
        registered.append(callback)
        return lambda: undone.append(callback)

    monkeypatch.setattr(cancellation, "_cancellation_waker", waker)

    def callback():
        return None

    end = cancellation.on_cancel(callback)
    end()
    assert registered == [callback] and undone == [callback]
