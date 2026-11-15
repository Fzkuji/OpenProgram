"""A message sent during the switch waits and runs after it."""
from __future__ import annotations

import json
from pathlib import Path
import time

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.operations.self_update.control import held_turns
from openprogram.operations.self_update.control.maintenance import enter_maintenance, leave_maintenance
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.conversation import turn_runtime as D
from openprogram.runtime.permissions.modes import PermissionRules


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SelfUpdateStore:
    from openprogram.state import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    target = SelfUpdateStore()
    target.create(UpdateRequest(
        update_id="su_held", session_id="s", origin_turn_id="t", origin_assistant_id="t_reply", agent_id="main",
        repo="/tmp/r", worktree_id="wt", base_sha="1" * 40, candidate_sha="2" * 40, changed_paths=("a.py",),
        pre_update_evidence=("git-status:clean",), goal="g", assertions=("a",), controller_version="old",
        task="t", kind="change",
    ))
    target.transition("su_held", UpdatePhase.BUILDING)
    target.transition("su_held", UpdatePhase.TRIAL)
    target.transition("su_held", UpdatePhase.READY)
    target.transition("su_held", UpdatePhase.QUIESCING)
    target.transition("su_held", UpdatePhase.ACTIVATING)
    enter_maintenance("su_held", deadline=time.time() + 300)
    return target


def _request(text: str = "hello", session: str = "session-1", source: str = "web") -> TurnRequest:
    return TurnRequest(session_id=session, user_text=text, agent_id="main", source=source)


def test_a_message_sent_during_the_switch_runs_after_it(store: SelfUpdateStore, monkeypatch) -> None:
    monkeypatch.setattr(D, "_run_loop_blocking",
                        lambda **_: (_ for _ in ()).throw(AssertionError("no turn runs during a switch")))
    events: list[dict] = []

    result = D._process_turn_once(_request("run me afterwards"), on_event=events.append)

    assert result.final_text == held_turns.HELD_MESSAGE and result.failed is False
    assert events[-1]["data"]["type"] == "result" and events[-1]["data"]["content"] == held_turns.HELD_MESSAGE
    assert held_turns.count(store) == 1

    ran: list[str] = []
    assert held_turns.drain(store=store, run=lambda v: ran.append(v["user_text"]), start=lambda f: f()) == 0
    assert ran == []

    leave_maintenance("su_held")
    assert held_turns.drain(store=store, run=lambda v: ran.append(v["user_text"]), start=lambda f: f()) == 1

    assert ran == ["run me afterwards"] and held_turns.count(store) == 0


def test_held_turns_of_one_conversation_keep_their_order(store: SelfUpdateStore) -> None:
    for text in ("first", "second", "third"):
        assert held_turns.hold(_request(text), store=store) is True
    leave_maintenance("su_held")
    ran: list[str] = []

    held_turns.drain(store=store, run=lambda v: ran.append(v["user_text"]), start=lambda f: f())

    assert ran == ["first", "second", "third"]


def test_a_saved_turn_keeps_its_authority_and_rules(store: SelfUpdateStore) -> None:
    request = _request()
    request.permission_rules = PermissionRules(deny=["bash"])
    request.principal_id = "owner-1"

    decoded = held_turns.decode(json.loads(json.dumps(held_turns.encode(request))))

    assert decoded.permission_rules.deny == ["bash"] and decoded.principal_id == "owner-1"


def test_a_saved_clean_run_still_starts_clean(store: SelfUpdateStore) -> None:
    request = _request()
    request.history_override = []

    assert held_turns.decode(json.loads(json.dumps(held_turns.encode(request)))).history_override == []
    assert held_turns.decode(json.loads(json.dumps(held_turns.encode(_request())))).history_override is None


def test_the_expired_marker_lets_turns_through(store: SelfUpdateStore) -> None:
    marker = store.root / "maintenance.json"
    marker.write_text(json.dumps({**json.loads(marker.read_text()), "deadline": time.time() - 1}))

    assert held_turns.hold(_request(), store=store) is False


def test_the_turn_check_is_not_held(store: SelfUpdateStore) -> None:
    from openprogram.operations.self_update.control.maintenance import turn_admission

    with turn_admission("self_update_turn_check") as admitted:
        assert admitted is True


def _held_and_open(store: SelfUpdateStore, text: str = "keep me", session: str = "session-1") -> None:
    assert held_turns.hold(_request(text, session), store=store)
    leave_maintenance("su_held")


def _drain(store: SelfUpdateStore, run) -> int:
    return held_turns.drain(store=store, run=run, start=lambda work: work())


@pytest.fixture
def answered_nothing(monkeypatch):
    monkeypatch.setattr(held_turns, "_already_answered", lambda values: False)


def test_a_turn_that_fails_is_kept_and_tried_again(store, answered_nothing) -> None:
    _held_and_open(store)
    ran: list[str] = []

    def fails_once(values):
        ran.append(values["user_text"])
        if len(ran) == 1:
            raise RuntimeError("the conversation store is not ready")

    _drain(store, fails_once)
    assert list((store.root / "held" / "running").glob("*.json")), "a turn that did not run is not deleted"
    _drain(store, fails_once)
    assert ran == ["keep me", "keep me"]
    assert not list((store.root / "held" / "running").glob("*.json"))


def test_a_turn_that_keeps_failing_moves_aside_and_the_owner_hears(store, answered_nothing, monkeypatch) -> None:
    from openprogram.operations.self_update.delivery import owner_notice

    told: list[str] = []
    monkeypatch.setattr(owner_notice, "tell", lambda notice, text: told.append(text) or True)
    _held_and_open(store)

    def always(values):
        raise TypeError("a field this version does not know")

    for _ in range(held_turns.MAX_ATTEMPTS):
        _drain(store, always)

    saved = list((store.root / "held" / "failed").glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["request"]["user_text"] == "keep me"
    assert not list((store.root / "held" / "running").glob("*.json"))
    assert len(told) == 1 and "keep me" in told[0] and str(saved[0]) in told[0]
    assert _drain(store, always) == 0, "it is not retried forever"


def test_a_stop_of_the_worker_mid_replay_leaves_the_turn(store, answered_nothing) -> None:
    from openprogram.models.providers.utils.errors import ExecInterrupt

    _held_and_open(store)

    def stopped(values):
        raise ExecInterrupt("worker_stopping")

    with pytest.raises(ExecInterrupt):
        _drain(store, stopped)

    assert len(list((store.root / "held" / "running").glob("*.json"))) == 1
    ran: list[str] = []
    _drain(store, lambda values: ran.append(values["user_text"]))
    assert ran == ["keep me"], "the next worker runs it"


def test_a_failure_holds_back_the_later_turns_of_that_conversation(store, answered_nothing) -> None:
    assert held_turns.hold(_request("first"), store=store)
    assert held_turns.hold(_request("second"), store=store)
    leave_maintenance("su_held")
    ran: list[str] = []

    def first_fails_once(values):
        ran.append(values["user_text"])
        if ran == ["first"]:
            raise RuntimeError("not yet")

    _drain(store, first_fails_once)
    assert ran == ["first"]
    _drain(store, first_fails_once)
    assert ran == ["first", "first", "second"]


def test_an_unreadable_file_does_not_stop_the_others(store, answered_nothing, monkeypatch) -> None:
    from openprogram.operations.self_update.delivery import owner_notice

    monkeypatch.setattr(owner_notice, "tell", lambda notice, text: True)
    assert held_turns.hold(_request("fine"), store=store)
    leave_maintenance("su_held")
    (store.root / "held" / "00000000000000000000-bad.json").write_text("{ not json")
    ran: list[str] = []

    _drain(store, lambda values: ran.append(values["user_text"]))

    assert ran == ["fine"]
    assert [path.name for path in (store.root / "held" / "failed").glob("*.json")] == ["00000000000000000000-bad.json"]
