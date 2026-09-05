"""Per-turn cancellation tokens.

The invariant under test: a stop trips the token of the turn running *now*
and nothing else. A token retired at turn end can never affect the next
turn, which is what removes the need for any cleanup-time flag reset.

See docs/reference/design/runtime/execution/execution-control.html.
"""

from __future__ import annotations

import threading

import pytest

from openprogram.agent import run_control as ps
from openprogram.agentic_programming.function import CancelledError


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts and ends with an empty token registry."""
    with ps._cancel_flags_lock:
        ps._current_tokens.clear()
        getattr(ps, "_cancel_cleanup_leases", {}).clear()
    ps.clear_turn_context()
    yield
    with ps._cancel_flags_lock:
        ps._current_tokens.clear()
        getattr(ps, "_cancel_cleanup_leases", {}).clear()
    ps.clear_turn_context()


# --- token lifecycle -------------------------------------------------------


def test_clear_turn_context_drops_all_bound_identifiers():
    ps.set_current_session_id("s1")
    ps.set_current_execution_id("exec-1")
    ps._current_token.set(ps.CancellationToken("s1", "exec-1"))

    ps.clear_turn_context()

    assert ps.get_current_session_id() is None
    assert ps.get_current_execution_id() is None
    assert ps._current_token.get(None) is None


def test_no_turn_means_nothing_to_cancel():
    """Between turns a stop is a no-op, not a flag that poisons what runs next."""
    ps.mark_cancelled("s1", execution_id="missing")
    assert ps.is_cancelled("s1") is False


def test_cancel_requires_an_exact_execution_id():
    with pytest.raises(TypeError):
        ps.mark_cancelled("s1")
    with pytest.raises(ValueError, match="execution_id is required"):
        ps.mark_cancelled("s1", execution_id="")


def test_cancel_trips_the_running_turn():
    token = ps.begin_turn("s1", "e1")
    ps.mark_cancelled("s1", execution_id="e1")
    assert token.is_cancelled() is True
    assert ps.current_token("s1", execution_id="e1").is_cancelled() is True


def test_stop_does_not_leak_into_the_next_turn():
    """The regression this design exists to prevent."""
    first = ps.begin_turn("s1", "e1")
    ps.mark_cancelled("s1", execution_id="e1")
    assert first.is_cancelled() is True

    ps.end_turn("s1", first)
    second = ps.begin_turn("s1", "e2")

    assert second.is_cancelled() is False
    assert ps.is_cancelled("s1") is False


def test_late_stop_cannot_reach_a_finished_turn():
    """A stop racing turn teardown lands on a retired token and dies there."""
    token = ps.begin_turn("s1", "e1")
    ps.end_turn("s1", token)

    ps.mark_cancelled("s1", execution_id="e1")

    assert token.is_cancelled() is False
    assert token.retired is True


def test_retired_token_reports_cancel_refused():
    token = ps.begin_turn("s1")
    assert token.cancel() is True
    token.retire()
    assert token.cancel() is False


def test_begin_turn_retires_the_previous_token():
    """A turn that never called end_turn cannot hold the session hostage."""
    stale = ps.begin_turn("s1")
    fresh = ps.begin_turn("s1")

    assert stale.retired is True
    assert fresh.retired is False
    assert ps.current_token("s1") is fresh


def test_end_turn_does_not_retire_a_successor():
    """Late teardown from turn N must not kill turn N+1."""
    first = ps.begin_turn("s1")
    second = ps.begin_turn("s1")

    ps.end_turn("s1", first)  # turn 1 finishing late

    assert ps.current_token("s1") is second
    assert second.retired is False


def test_sessions_are_independent():
    a = ps.begin_turn("sA", "eA")
    b = ps.begin_turn("sB", "eB")
    ps.mark_cancelled("sA", execution_id="eA")

    assert a.is_cancelled() is True
    assert b.is_cancelled() is False


# --- the signal every layer checks ----------------------------------------


def test_registered_event_is_the_token_event():
    """Call sites owning an Event still get one token everything shares."""
    ev = threading.Event()
    ps.register_cancel_event("s1", ev, execution_id="e1")

    ps.mark_cancelled("s1", execution_id="e1")

    assert ev.is_set() is True, "the LLM call / tool layer waits on this Event"
    assert ps.current_token("s1", execution_id="e1").is_cancelled() is True


def test_exact_cancel_without_persisted_execution_still_trips_token():
    event = threading.Event()
    ps.register_cancel_event("s1", event, execution_id="missing-execution")
    try:
        ps.mark_cancelled("s1", execution_id="missing-execution")
        assert event.is_set() is True
    finally:
        ps.unregister_cancel_event(
            "s1", event, execution_id="missing-execution",
        )


def test_exact_cancel_persistence_failure_only_trips_target_token(monkeypatch):
    target = threading.Event()
    sibling = threading.Event()
    ps.register_cancel_event("s1", target, execution_id="target")
    ps.register_cancel_event("s1", sibling, execution_id="sibling")

    def fail_cancel(_execution_id: str) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr(ps, "cancel_execution", fail_cancel)
    try:
        ps.mark_cancelled("s1", execution_id="target")
        assert target.is_set() is True
        assert sibling.is_set() is False
    finally:
        ps.unregister_cancel_event("s1", target, execution_id="target")
        ps.unregister_cancel_event("s1", sibling, execution_id="sibling")


def test_exact_cancel_rejected_as_terminal_does_not_trip_stale_token(
    monkeypatch,
):
    event = threading.Event()
    ps.register_cancel_event("s1", event, execution_id="completed")

    def reject_cancel(execution_id: str) -> None:
        raise ps.ExecutionNotCancellable(execution_id)

    monkeypatch.setattr(ps, "cancel_execution", reject_cancel)
    try:
        ps.mark_cancelled("s1", execution_id="completed")
        assert event.is_set() is False
    finally:
        ps.unregister_cancel_event(
            "s1", event, execution_id="completed",
        )


def test_tripping_the_event_directly_is_visible_as_cancelled():
    ev = threading.Event()
    ps.register_cancel_event("s1", ev, execution_id="e1")
    ev.set()
    assert ps.current_token("s1", execution_id="e1").is_cancelled() is True


def test_clear_cancel_retires_rather_than_resets():
    ev = threading.Event()
    ps.register_cancel_event("s1", ev, execution_id="e1")
    ps.mark_cancelled("s1", execution_id="e1")

    ps.clear_cancel("s1")

    assert ps.current_token("s1") is None
    assert ps.is_cancelled("s1") is False


def test_unregister_with_event_leaves_newer_turn_alone():
    """An older turn finishing late must not pop the newer turn's token.

    Trigger chain: /task --async registers ev_task, the user starts a
    chat turn which registers ev_chat (replacing + retiring ev_task's
    token), then the task ends and unregisters. With the Event passed,
    the mismatch is detected and the chat turn's token survives — Stop
    still works.
    """
    ev_task = threading.Event()
    ev_chat = threading.Event()
    ps.register_cancel_event("s1", ev_task, execution_id="e1")
    ps.register_cancel_event("s1", ev_chat, execution_id="e2")

    ps.unregister_cancel_event("s1", ev_task, execution_id="e1")  # task ends late

    token = ps.current_token("s1", execution_id="e2")
    assert token is not None, "chat turn's registration was popped"
    ps.mark_cancelled("s1", execution_id="e2")
    assert ev_chat.is_set() is True, "Stop no longer reaches the chat turn"
    assert ev_task.is_set() is False


def test_unregister_with_matching_event_pops_own_registration():
    ev = threading.Event()
    ps.register_cancel_event("s1", ev, execution_id="e1")

    ps.unregister_cancel_event("s1", ev)

    assert ps.current_token("s1") is None
    ps.mark_cancelled("s1", execution_id="missing")  # no-op between turns
    assert ev.is_set() is False


def test_unregister_without_event_keeps_force_clear_semantics():
    """Internal cleanup may explicitly clear the current registration."""
    ev = threading.Event()
    ps.register_cancel_event("s1", ev, execution_id="e1")

    ps.unregister_cancel_event("s1")

    assert ps.current_token("s1") is None


def test_exact_cleanup_lease_rejects_handover_until_release():
    old_event = threading.Event()
    new_event = threading.Event()
    ps.register_cancel_event("s1", old_event)

    assert ps.acquire_cancel_cleanup("s1", old_event) is True
    assert ps.claim_cancel_event("s1", new_event) is False
    with pytest.raises(RuntimeError):
        ps.register_cancel_event("s1", new_event)
    with pytest.raises(RuntimeError):
        ps.begin_turn("s1")
    assert ps.current_token("s1").event is old_event

    ps.unregister_cancel_event("s1", old_event)
    ps.release_cancel_cleanup("s1", old_event)
    ps.register_cancel_event("s1", new_event)

    assert ps.current_token("s1").event is new_event


# --- enforcement: every frame in the turn checks the one token -------------


def test_cancel_hook_raises_inside_a_cancelled_turn():
    """@agentic_function entry and Runtime.exec go through this hook."""
    ps.begin_turn("s1", "e1")
    tok = ps.set_current_session_id("s1")
    try:
        ps._cancel_hook()  # not cancelled yet → no raise
        ps.mark_cancelled("s1", execution_id="e1")
        with pytest.raises(CancelledError):
            ps._cancel_hook()
    finally:
        ps.reset_current_session_id(tok)


def test_check_cancelled_matches_the_hook():
    """Long-running tool bodies poll this between heavy stages."""
    ps.begin_turn("s1", "e1")
    tok = ps.set_current_session_id("s1")
    try:
        ps.mark_cancelled("s1", execution_id="e1")
        with pytest.raises(CancelledError):
            ps.check_cancelled()
    finally:
        ps.reset_current_session_id(tok)


def test_hook_is_silent_after_the_turn_ends():
    """Work continuing past turn end is not killed by that turn's stop."""
    token = ps.begin_turn("s1", "e1")
    tok = ps.set_current_session_id("s1")
    try:
        ps.mark_cancelled("s1", execution_id="e1")
        ps.end_turn("s1", token)
        ps.check_cancelled()  # must not raise
    finally:
        ps.reset_current_session_id(tok)


def test_hook_is_a_noop_with_no_session_bound():
    """CLI / tests / headless run outside any turn."""
    ps.check_cancelled()


def test_context_bound_token_wins_over_the_registry():
    """A nested frame checks its own turn's token, not whatever is current.

    Cancelling the frame's own turn must stop that frame even though the
    session registry has already moved on to a newer turn.
    """
    mine = ps.begin_turn("s1", "e1")
    ps.mark_cancelled("s1", execution_id="e1")  # stop aimed at THIS turn

    tok_t = ps._current_token.set(mine)
    tok_s = ps.set_current_session_id("s1")
    try:
        # The session hands over to a fresh turn while this frame is live.
        ps.begin_turn("s1", "e2")
        assert ps.is_cancelled("s1") is False, "registry moved to a clean turn"

        # The frame still belongs to the cancelled turn, so it must abort.
        with pytest.raises(CancelledError):
            ps.check_cancelled()
    finally:
        ps.reset_current_session_id(tok_s)
        ps._current_token.reset(tok_t)


def test_a_new_turn_does_not_cancel_a_frame_of_the_old_one():
    """The mirror case: an uncancelled frame stays uncancelled."""
    mine = ps.begin_turn("s1", "e1")
    tok_t = ps._current_token.set(mine)
    tok_s = ps.set_current_session_id("s1")
    try:
        ps.begin_turn("s1", "e2")
        ps.mark_cancelled("s1", execution_id="e2")  # stops the NEW turn, not this frame
        ps.check_cancelled()  # must not raise
    finally:
        ps.reset_current_session_id(tok_s)
        ps._current_token.reset(tok_t)


# --- no thread leak --------------------------------------------------------


def test_cancel_bridge_thread_exits_when_the_turn_ends():
    """The dispatcher's thread→asyncio bridge must not park forever.

    Mirrors the bridge in dispatcher._run_loop_blocking: a watcher thread
    waits on the turn's Event and exits once the turn is over.
    """
    cancel_event = threading.Event()
    turn_over = threading.Event()
    fired: list[bool] = []

    def _watch():
        while not (cancel_event.wait(0.01) or turn_over.is_set()):
            pass
        if cancel_event.is_set():
            fired.append(True)

    t = threading.Thread(target=_watch, name="turn-cancel-bridge")
    t.start()

    turn_over.set()  # the turn finished without being cancelled
    t.join(timeout=2.0)

    assert not t.is_alive(), "bridge thread leaked past the end of the turn"
    assert fired == [], "bridge must not report a cancel that never happened"


def test_cancel_bridge_thread_reports_a_real_cancel():
    cancel_event = threading.Event()
    turn_over = threading.Event()
    fired: list[bool] = []

    def _watch():
        while not (cancel_event.wait(0.01) or turn_over.is_set()):
            pass
        if cancel_event.is_set():
            fired.append(True)

    t = threading.Thread(target=_watch, name="turn-cancel-bridge")
    t.start()
    cancel_event.set()
    t.join(timeout=2.0)

    assert not t.is_alive()
    assert fired == [True]


def test_many_turns_leave_no_live_threads():
    """Thread count returns to baseline after a run of turns."""
    baseline = threading.active_count()
    threads = []
    for _ in range(20):
        cancel_event = threading.Event()
        turn_over = threading.Event()

        def _watch(ce=cancel_event, to=turn_over):
            while not (ce.wait(0.01) or to.is_set()):
                pass

        t = threading.Thread(target=_watch, name="turn-cancel-bridge")
        t.start()
        threads.append((t, turn_over))

    for t, turn_over in threads:
        turn_over.set()
    for t, _ in threads:
        t.join(timeout=2.0)

    assert threading.active_count() <= baseline, "threads leaked across turns"
