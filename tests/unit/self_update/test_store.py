from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from openprogram.self_update import (
    ActiveUpdateError,
    ConcurrentUpdateError,
    CorruptUpdateStateError,
    InvalidTransitionError,
    IterationMode,
    IterationPolicy,
    SelfUpdateStore,
    UpdatePhase,
    UpdateRequest,
    mint_update_id,
)


def _request(update_id: str = "su_test") -> UpdateRequest:
    return UpdateRequest(
        update_id=update_id,
        session_id="session-1",
        origin_turn_id="turn-1",
        origin_assistant_id="assistant-1",
        agent_id="default",
        repo=str(Path("/tmp/openprogram").resolve()),
        worktree_id="wt-1",
        base_sha="1" * 40,
        candidate_sha="2" * 40,
        changed_paths=("openprogram/example.py",),
        pre_update_evidence=("pytest:tests/unit/example.py",),
        goal="Add the requested behavior",
        assertions=("The public entry returns the new result",),
    )


def test_create_persists_private_record_and_rejects_second_active(
    tmp_path: Path,
) -> None:
    store = SelfUpdateStore(tmp_path)

    state = store.create(_request())

    assert state.phase is UpdatePhase.PREPARING
    assert store.load_active().request.update_id == "su_test"
    assert json.loads((tmp_path / "active.json").read_text())["update_id"] == "su_test"
    from openprogram._compat import user_private_metadata
    assert user_private_metadata((tmp_path / "su_test" / "request.json").stat(), exact_mode=0o600)
    assert user_private_metadata((tmp_path / "su_test").stat(), exact_mode=0o700)
    with pytest.raises(ActiveUpdateError):
        store.create(_request("su_other"))


def test_transition_is_compare_and_swap_and_rejects_illegal_edges(
    tmp_path: Path,
) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())

    staged = store.transition(
        "su_test", UpdatePhase.STAGING, expected_phase=UpdatePhase.PREPARING
    )
    assert staged.phase is UpdatePhase.STAGING
    assert staged.revision == 2

    with pytest.raises(ConcurrentUpdateError):
        store.transition(
            "su_test", UpdatePhase.READY, expected_phase=UpdatePhase.PREPARING
        )
    with pytest.raises(InvalidTransitionError):
        store.transition("su_test", UpdatePhase.VERIFYING)
    with pytest.raises(ValueError):
        store.transition("su_test", "ready")  # type: ignore[arg-type]


def test_verifier_claim_is_stable_and_lease_can_be_taken_over(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    for current, target in (
        (UpdatePhase.PREPARING, UpdatePhase.STAGING),
        (UpdatePhase.STAGING, UpdatePhase.READY),
        (UpdatePhase.READY, UpdatePhase.ACTIVATING),
        (UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING),
    ):
        store.transition("su_test", target, expected_phase=current)

    first = store.claim_verifier("su_test", owner="worker-a", now=100, lease_seconds=30)
    duplicate = store.claim_verifier(
        "su_test", owner="worker-b", now=110, lease_seconds=30
    )
    takeover = store.claim_verifier(
        "su_test", owner="worker-b", now=131, lease_seconds=30
    )

    assert first.acquired is True
    assert duplicate.acquired is False
    assert takeover.acquired is True
    assert first.job_id == duplicate.job_id == takeover.job_id
    assert takeover.generation == 2


def test_corrupt_and_future_schema_fail_closed(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    state_path = tmp_path / "su_test" / "state.json"

    state_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(CorruptUpdateStateError):
        store.load("su_test")

    state_path.write_text(json.dumps({"schema": 999}), encoding="utf-8")
    with pytest.raises(CorruptUpdateStateError):
        store.load("su_test")

    event_store = SelfUpdateStore(tmp_path / "events")
    event_store.create(_request())
    (tmp_path / "events" / "su_test" / "events.jsonl").write_text(
        "not-json\n", encoding="utf-8"
    )
    with pytest.raises(CorruptUpdateStateError):
        event_store.load("su_test")


@pytest.mark.parametrize("active_id", ("../escape", "su_missing"))
def test_invalid_active_pointer_is_reported_as_corrupt(
    tmp_path: Path, active_id: str
) -> None:
    (tmp_path / "active.json").write_text(
        json.dumps({"schema": 1, "update_id": active_id}), encoding="utf-8"
    )

    with pytest.raises(CorruptUpdateStateError):
        SelfUpdateStore(tmp_path).load_active()


@pytest.mark.parametrize(
    ("field", "value"),
    (("dispatch", "claimed"), ("revision", -1), ("detail", [])),
)
def test_malformed_state_fields_fail_closed(
    tmp_path: Path, field: str, value: object
) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    state_path = tmp_path / "su_test" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state[field] = value
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(CorruptUpdateStateError):
        store.load("su_test")


@pytest.mark.parametrize("field", ("detail", "last_event"))
def test_non_standard_json_numbers_fail_closed(tmp_path: Path, field: str) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    state_path = tmp_path / "su_test" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if field == "detail":
        state[field] = {"nested": {"value": float("nan")}}
    else:
        state[field]["nested"] = {"value": float("inf")}
    state_path.write_text(json.dumps(state, allow_nan=True), encoding="utf-8")

    with pytest.raises(CorruptUpdateStateError):
        store.load("su_test")


def test_terminal_transition_releases_active_slot(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    store.transition("su_test", UpdatePhase.ABORTED)

    assert store.load_active() is None
    assert store.create(_request("su_next")).phase is UpdatePhase.PREPARING


def test_request_rejects_unsafe_changed_paths() -> None:
    with pytest.raises(ValueError):
        replace(_request(), changed_paths=("../outside.py",))


def test_request_round_trip_keeps_evidence_and_iteration_policy() -> None:
    request = replace(
        _request(),
        iteration_policy=IterationPolicy(
            mode=IterationMode.BOUNDED_AUTO,
            max_attempts=2,
            deadline=1234,
            allowed_paths=("openprogram/self_update/**",),
            required_tests=("python -m pytest -q tests/unit/self_update",),
        ),
    )

    restored = UpdateRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.pre_update_evidence == ("pytest:tests/unit/example.py",)
    assert restored.iteration_policy.mode is IterationMode.BOUNDED_AUTO


def test_create_recovers_missing_active_pointer_and_blocks_second_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SelfUpdateStore(tmp_path)
    write_json = store._write_json

    def fail_active(path: Path, value: object) -> None:
        if path.name == "active.json":
            raise OSError("injected active pointer failure")
        write_json(path, value)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "_write_json", fail_active)
    with pytest.raises(OSError, match="active pointer failure"):
        store.create(_request())
    monkeypatch.setattr(store, "_write_json", write_json)

    restored = SelfUpdateStore(tmp_path).load("su_test")
    assert restored.state.phase is UpdatePhase.PREPARING
    assert json.loads((tmp_path / "active.json").read_text())["update_id"] == "su_test"
    with pytest.raises(ActiveUpdateError):
        store.create(_request("su_other"))


def test_load_repairs_state_committed_before_event_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    write_events = store._write_events
    monkeypatch.setattr(
        store,
        "_write_events",
        lambda _path, _event: (_ for _ in ()).throw(OSError("injected event failure")),
    )

    with pytest.raises(OSError, match="event failure"):
        store.transition("su_test", UpdatePhase.STAGING)
    monkeypatch.setattr(store, "_write_events", write_events)

    record = store.load("su_test")
    events = [
        json.loads(line)
        for line in (tmp_path / "su_test" / "events.jsonl").read_text().splitlines()
    ]
    assert record.state.phase is UpdatePhase.STAGING
    assert [event["revision"] for event in events] == [1, 2]
    assert events[-1] == record.state.last_event


def test_event_log_rejects_illegal_transition_evidence(tmp_path: Path) -> None:
    store = SelfUpdateStore(tmp_path)
    store.create(_request())
    store.transition("su_test", UpdatePhase.STAGING)
    store.transition("su_test", UpdatePhase.READY)
    events_path = tmp_path / "su_test" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    events[1]["from"] = UpdatePhase.SUCCEEDED.value
    events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    with pytest.raises(CorruptUpdateStateError):
        store.load("su_test")


def test_claim_rejects_non_finite_time_and_update_ids_are_valid() -> None:
    assert mint_update_id().startswith("su_")
    with pytest.raises(ValueError):
        SelfUpdateStore(Path("/tmp/unused")).claim_verifier(
            "su_test", owner="worker", now=float("nan"), lease_seconds=30
        )
