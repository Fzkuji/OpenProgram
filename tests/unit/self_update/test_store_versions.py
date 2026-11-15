"""What schema 2 adds to the store: retirement, the queue, the candidate guard."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from openprogram.operations.self_update import SelfUpdateStore, UpdatePhase, UpdateRequest
from openprogram.operations.self_update import types as update_types
from openprogram.operations.self_update.types import ActiveUpdateError, InvalidTransitionError, parse_phase


def _request(update_id: str = "su_one", **overrides) -> UpdateRequest:
    return UpdateRequest(
        update_id=update_id, session_id="session", origin_turn_id="turn", origin_assistant_id="turn_reply",
        agent_id="main", repo="/tmp/repo", worktree_id="wt", base_sha="1" * 40, candidate_sha="2" * 40,
        changed_paths=("a.py",), pre_update_evidence=("git-status:clean",), goal="goal", assertions=("works",),
        **overrides,
    )


def _downgrade(store: SelfUpdateStore, update_id: str, **request_fields) -> None:
    """Rewrite the files as schema 1 wrote them."""
    directory = store.root / update_id
    request = json.loads((directory / "request.json").read_text())
    for key in ("kind", "controller_version", "task", "candidate_version", "rebases"):
        request.pop(key, None)
    request.update(schema=1, app_path="/Applications/OpenProgram.app", **request_fields)
    (directory / "request.json").write_text(json.dumps(request))


def test_a_schema_one_update_still_running_is_retired_with_a_note(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    store.transition("su_one", UpdatePhase.BUILDING)
    _downgrade(store, "su_one")

    record = store.load("su_one")

    assert record.state.phase is UpdatePhase.ABORTED
    assert "previous update system" in record.state.detail["note"]
    assert store.load_active() is None


def test_a_finished_schema_one_update_is_shown_as_it_ended(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    store.transition("su_one", UpdatePhase.ABORTED)
    before = store.load("su_one").state
    _downgrade(store, "su_one")

    assert store.load("su_one").state == before


def test_old_phase_names_are_read(tmp_path: Path) -> None:
    assert parse_phase("staging") is UpdatePhase.BUILDING


def test_unknown_keys_written_by_a_newer_version_are_ignored(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    path = store.root / "su_one" / "request.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "field_from_the_future": 1}))

    assert store.load("su_one").request.update_id == "su_one"


def test_requests_wait_in_order_and_start_at_building(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    assert store.enqueue(_request("su_two"), {}) == 1
    assert store.enqueue(_request("su_three"), {}) == 2
    assert [item["update_id"] for item in store.queued()] == ["su_two", "su_three"]
    assert store.take_queued() is None  # The slot is taken.

    store.transition("su_one", UpdatePhase.ABORTED)
    state = store.take_queued()

    assert state.update_id == "su_two" and state.phase is UpdatePhase.BUILDING
    assert [item["position"] for item in store.queued()] == [1]


def test_a_queued_request_can_be_removed(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    store.enqueue(_request("su_two"), {})

    assert store.remove_queued("su_two") is True
    assert store.remove_queued("su_two") is False and store.queued() == []


def test_a_queued_request_whose_turn_never_committed_is_dropped(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.enqueue(_request("su_two"), {})
    store.enqueue(_request("su_three"), {})

    state = store.take_queued(durable=lambda request: request.update_id != "su_two")

    assert state.update_id == "su_three"


def test_only_one_update_is_active(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    with pytest.raises(ActiveUpdateError):
        store.create(_request("su_two"))


def _on_activating(store: SelfUpdateStore) -> None:
    store.transition("su_one", UpdatePhase.BUILDING)
    store.transition("su_one", UpdatePhase.TRIAL, detail={"candidate_version": "new"})
    store.transition("su_one", UpdatePhase.READY, detail={"candidate_version": "new"})
    store.transition("su_one", UpdatePhase.QUIESCING, detail={"candidate_version": "new"})
    store.transition("su_one", UpdatePhase.ACTIVATING, detail={"candidate_version": "new"})


def test_the_candidates_own_worker_cannot_move_its_update(tmp_path: Path, monkeypatch) -> None:
    from openprogram.operations.self_update.versions import layout

    store = SelfUpdateStore(tmp_path)
    store.create(_request(controller_version="old", kind="change", task="t"))
    _on_activating(store)
    monkeypatch.setattr(layout, "current_runtime", lambda *_: layout.CurrentRuntime(layout.Layout(tmp_path), "new"))

    with pytest.raises(InvalidTransitionError):
        store.transition("su_one", UpdatePhase.SUCCEEDED)
    assert store.load("su_one").state.phase is UpdatePhase.ACTIVATING


def test_the_old_versions_controller_can_move_it(tmp_path: Path, monkeypatch) -> None:
    from openprogram.operations.self_update.versions import layout

    store = SelfUpdateStore(tmp_path)
    store.create(_request(controller_version="old", kind="change", task="t"))
    _on_activating(store)
    monkeypatch.setattr(layout, "current_runtime", lambda *_: layout.CurrentRuntime(layout.Layout(tmp_path), "old"))

    assert store.transition("su_one", UpdatePhase.VERIFYING).phase is UpdatePhase.VERIFYING


def test_a_queued_go_back_skips_the_build_and_takes_the_current_tip(tmp_path) -> None:
    store = SelfUpdateStore(tmp_path / "self-updates")
    store.enqueue(_request("su_back", controller_version="v1", candidate_version="v0", kind="undo_to"), {})
    state = store.take_queued(local_tip=lambda: "9" * 40)
    assert state.phase is UpdatePhase.TRIAL and state.detail["base_sha"] == "9" * 40


def test_a_queued_change_still_builds(tmp_path) -> None:
    store = SelfUpdateStore(tmp_path / "self-updates")
    store.enqueue(_request("su_change", controller_version="v1", task="t", kind="change"), {})
    state = store.take_queued(local_tip=lambda: "9" * 40)
    assert state.phase is UpdatePhase.BUILDING and "base_sha" not in state.detail
