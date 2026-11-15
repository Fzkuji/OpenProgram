"""The user's front application and window order are kept while an agent operates another one."""
from __future__ import annotations

from openprogram.host._platform import front

USER, OTHER, AGENT = 10, 20, 30
FULL, LEFT, RIGHT = (0.0, 0.0, 1000.0, 800.0), (0.0, 0.0, 500.0, 800.0), (500.0, 0.0, 500.0, 800.0)


class Screen(front._Desktop):
    """A desktop whose front application and window stack the test sets."""

    def __init__(self, front_pid: int, stack: list[tuple[int, int]]) -> None:
        self.front_pid, self.windows = front_pid, [(pid, window, FULL) for pid, window in stack]
        self.activated: list[int] = []
        self.raised: list[tuple[int, int]] = []

    def front(self):
        return self.front_pid

    def name(self, pid):
        return f"app {pid}"

    def stack(self):
        return list(self.windows)

    def activate(self, pid):
        self.activated.append(pid)
        self.front_pid = pid
        return True

    def raise_window(self, pid, window_id):
        self.raised.append((pid, window_id))


def keeper(screen: Screen) -> tuple[front._Keeper, front.Watch]:
    watch = front.Watch()
    return front._Keeper(screen, lambda pid: pid == AGENT, watch), watch


def test_an_operated_application_that_takes_the_front_gives_it_back():
    screen = Screen(USER, [(USER, 1), (OTHER, 2), (AGENT, 3)])
    keep, watch = keeper(screen)
    screen.front_pid = AGENT
    keep.check()
    assert screen.front_pid == USER
    assert [taken.application for taken in watch.taken] == [f"app {AGENT}"]


def test_the_windows_that_stood_over_it_are_raised_again_deepest_first():
    screen = Screen(USER, [(USER, 1), (OTHER, 2), (AGENT, 3), (OTHER, 4)])
    keep, _ = keeper(screen)
    screen.front_pid = AGENT
    keep.check()
    assert screen.raised == [(OTHER, 2), (USER, 1)]


def test_windows_between_two_of_its_own_keep_their_place():
    before = [(USER, 1, FULL), (AGENT, 2, FULL), (OTHER, 3, FULL), (AGENT, 4, FULL), (OTHER, 5, FULL)]
    assert front.restack(before, lambda pid: pid == AGENT) == [(OTHER, 3), (AGENT, 2), (USER, 1)]


def test_a_window_that_touches_none_of_its_windows_is_left_alone():
    before = [(USER, 1, FULL), (OTHER, 2, LEFT), (OTHER, 3, RIGHT), (AGENT, 4, RIGHT)]
    assert front.restack(before, lambda pid: pid == AGENT) == [(OTHER, 3), (USER, 1)]


def test_an_application_with_no_window_on_screen_leaves_the_order_alone():
    assert front.restack([(USER, 1, FULL), (OTHER, 2, FULL)], lambda pid: pid == AGENT) == []


def test_an_application_the_user_switches_to_is_left_in_front():
    screen = Screen(USER, [(USER, 1), (OTHER, 2), (AGENT, 3)])
    keep, watch = keeper(screen)
    screen.front_pid = OTHER
    keep.check()
    assert screen.front_pid == OTHER and not screen.activated and not watch.taken
    # The front then goes back to where the user went, not to where they were.
    screen.front_pid = AGENT
    keep.check()
    assert screen.front_pid == OTHER


def test_an_application_that_keeps_taking_the_front_is_fought_a_bounded_number_of_times():
    screen = Screen(USER, [(USER, 1), (AGENT, 3)])
    keep, watch = keeper(screen)
    for _ in range(front.MAX_RETURNS + 3):
        screen.front_pid = AGENT
        keep.check()
    assert len(watch.taken) == front.MAX_RETURNS
    assert screen.front_pid == AGENT


def test_without_accessibility_the_front_application_is_still_given_back():
    class NoAccessibility(Screen):
        def raise_window(self, pid, window_id):
            raise PermissionError("Accessibility is not granted")

    screen = NoAccessibility(USER, [(USER, 1), (OTHER, 2), (AGENT, 3)])
    keep, watch = keeper(screen)
    screen.front_pid = AGENT
    keep.check()
    assert screen.front_pid == USER and len(watch.taken) == 1


def test_nothing_is_watched_when_the_user_is_in_the_operated_application(monkeypatch):
    screen = Screen(AGENT, [(AGENT, 3), (USER, 1)])
    monkeypatch.setattr(front, "desktop", lambda: screen)
    with front.kept(lambda pid: pid == AGENT):
        screen.front_pid = AGENT
    assert not screen.activated


def test_a_call_that_operates_nothing_on_this_desktop_is_not_watched(monkeypatch):
    screen = Screen(USER, [(USER, 1)])
    monkeypatch.setattr(front, "desktop", lambda: screen)
    with front.kept(None) as watch:
        screen.front_pid = AGENT
    assert not screen.activated and not watch.taken


LOST, GAINED, APPKIT = front.EVENT_LOST, front.EVENT_GAINED, front.EVENT_APPKIT


def test_a_theft_is_answered_inside_the_event_and_neither_application_is_told():
    screen = Screen(USER, [(USER, 1), (AGENT, 3)])
    keep, watch = keeper(screen)
    screen.front_pid = AGENT
    # The window server's order for a running application that activates itself, then for the front given back.
    dropped = [keep.event(LOST, 0, USER), keep.event(GAINED, 0, AGENT),
               keep.event(LOST, 0, AGENT), keep.event(GAINED, 0, USER)]
    assert dropped == [True, True, True, True]
    assert screen.front_pid == USER and not screen.raised and len(watch.taken) == 1
    # What follows is not part of it.
    assert not keep.event(GAINED, 0, AGENT) and not keep.event(LOST, 0, AGENT)


def test_an_application_that_starts_in_front_is_told_with_appkit_events_and_they_are_dropped_too():
    screen = Screen(USER, [(USER, 1)])
    keep, _ = keeper(screen)
    screen.front_pid = AGENT
    assert keep.event(LOST, 0, USER)
    assert keep.event(APPKIT, front.APPKIT_ACTIVATED, AGENT)
    assert not keep.event(APPKIT, 22, USER) and not keep.event(APPKIT, 4, AGENT)
    assert keep.event(APPKIT, front.APPKIT_DEACTIVATED, AGENT)
    assert keep.event(GAINED, 0, USER)


def test_events_pass_when_the_user_switches_applications_themselves():
    screen = Screen(USER, [(USER, 1), (OTHER, 2), (AGENT, 3)])
    keep, watch = keeper(screen)
    screen.front_pid = OTHER
    assert not keep.event(LOST, 0, USER) and not keep.event(GAINED, 0, OTHER)
    assert not screen.activated and not watch.taken


def test_events_pass_once_the_front_is_no_longer_given_back():
    screen = Screen(USER, [(USER, 1), (AGENT, 3)])
    keep, _ = keeper(screen)
    keep.returns = front.MAX_RETURNS
    screen.front_pid = AGENT
    assert not keep.event(LOST, 0, USER) and not keep.event(GAINED, 0, AGENT)
    assert screen.front_pid == AGENT


def test_an_event_is_dropped_only_when_the_front_was_really_given_back():
    class Refuses(Screen):
        def activate(self, pid):
            return False

    screen = Refuses(USER, [(USER, 1), (AGENT, 3)])
    keep, watch = keeper(screen)
    screen.front_pid = AGENT
    assert not keep.event(LOST, 0, USER) and not keep.event(GAINED, 0, AGENT) and not watch.taken


def test_a_window_moved_after_an_unseen_return_is_put_back():
    screen = Screen(USER, [(USER, 1), (OTHER, 2), (AGENT, 3)])
    keep, _ = keeper(screen)
    screen.front_pid = AGENT
    assert keep.event(LOST, 0, USER)
    keep.check()
    assert not screen.raised
    screen.windows = [(AGENT, 3, FULL), (USER, 1, FULL), (OTHER, 2, FULL)]
    keep.check()
    assert screen.raised == [(OTHER, 2), (USER, 1)]


def test_with_events_held_the_reading_acts_only_on_a_theft_that_lasts():
    screen = Screen(USER, [(USER, 1), (AGENT, 3)])
    keep, watch = keeper(screen)
    keep.tapped = True
    screen.front_pid = AGENT
    keep.check()
    assert screen.front_pid == AGENT and not watch.taken
    keep.check()
    assert screen.front_pid == USER and len(watch.taken) == 1


def test_disturbed_only_when_an_operated_window_stands_over_one_that_stood_over_it():
    before = [(USER, 1, FULL), (OTHER, 2, FULL), (AGENT, 3, FULL)]
    agent = lambda pid: pid == AGENT
    assert not front.disturbed(before, before, agent)
    # The user's own reordering is theirs.
    assert not front.disturbed(before, [(OTHER, 2, FULL), (USER, 1, FULL), (AGENT, 3, FULL)], agent)
    assert front.disturbed(before, [(USER, 1, FULL), (AGENT, 3, FULL), (OTHER, 2, FULL)], agent)
    assert not front.disturbed([(AGENT, 3, FULL), (USER, 1, FULL)], [(AGENT, 3, FULL), (USER, 1, FULL)], agent)


def test_one_tap_serves_every_step_and_ends_with_the_last():
    class Listening(Screen):
        listening = 0

        def listen(self, handler):
            Listening.listening += 1
            self.handler = handler
            return lambda: setattr(Listening, "listening", Listening.listening - 1)

    screen = Listening(USER, [(USER, 1), (AGENT, 3)])
    shared = front._Interception()
    first, second = keeper(screen)[0], keeper(screen)[0]
    assert shared.join(screen, first) and shared.join(screen, second) and Listening.listening == 1
    screen.front_pid = AGENT
    assert screen.handler(LOST, 0, USER)
    shared.leave(first)
    assert Listening.listening == 1
    shared.leave(second)
    assert Listening.listening == 0
    assert not front._Interception().join(Screen(USER, [(USER, 1)]), first)


def test_a_backend_holding_the_window_forward_on_purpose_is_left_to_it():
    screen = Screen(USER, [(USER, 1), (AGENT, 3)])
    keep, watch = keeper(screen)
    with front.forward():
        screen.front_pid = AGENT
        keep.check()
        assert screen.front_pid == AGENT and not watch.taken
    # The backend put the user's window back itself; anything left after it is a theft again.
    keep.check()
    assert screen.front_pid == USER and len(watch.taken) == 1


def test_while_an_application_is_being_started_a_process_with_no_window_before_is_the_new_one():
    NEW = 40
    screen = Screen(USER, [(USER, 1), (OTHER, 2)])
    watch = front.Watch()
    keep = front._Keeper(screen, lambda pid: False, watch)
    with front.starting():
        screen.front_pid = NEW
        keep.check()
    assert screen.front_pid == USER and len(watch.taken) == 1
    # Outside a start, a new process in front is the user's own doing.
    screen.front_pid = NEW
    keep.check()
    assert screen.front_pid == NEW


def test_an_application_that_was_running_is_the_users_own_switch_even_during_a_start():
    screen = Screen(USER, [(USER, 1), (OTHER, 2)])
    keep = front._Keeper(screen, lambda pid: False, front.Watch())
    with front.starting():
        screen.front_pid = OTHER
        keep.check()
    assert screen.front_pid == OTHER and not screen.activated


def test_off_macos_an_application_is_the_process_the_backend_named(monkeypatch):
    monkeypatch.setattr(front.sys, "platform", "win32")
    monkeypatch.setattr(front, "_noted", set())
    operated = front.named("Notepad")
    assert not operated(AGENT)
    front.note(AGENT)
    assert operated(AGENT) and not operated(USER)


def test_a_browser_is_told_by_its_executable_off_macos(monkeypatch):
    screen = Screen(USER, [(USER, 1)])
    screen.name = lambda pid: {AGENT: "Chrome", USER: "Code"}.get(pid, "")
    monkeypatch.setattr(front, "desktop", lambda: screen)
    operated = front.programs("chrome", "google-chrome")
    assert operated(AGENT) and not operated(USER)
