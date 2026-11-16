from __future__ import annotations

import json
import time

import pytest

from openprogram.runtime.execution.attempts import (
    AttemptConflict,
    AttemptStatus,
    AttemptStore,
    FAILURE_TEXT_CHARS,
    stored_failure_text,
)
from openprogram.runtime.execution.model import CapabilitySet, ExecutionStatus
from openprogram.runtime.execution.store import ExecutionStore


class Clock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _execution(tmp_path):
    execution_store = ExecutionStore(tmp_path / "executions.db")
    revision = execution_store.create_revision(manifest={"entrypoint": "chat"})
    execution = execution_store.create_execution(
        execution_id="exec_1",
        run_id="run_1",
        session_id="session_1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("action.before", "action.after"),
            state_schema_version=1,
        ),
    )
    return execution_store, execution


def test_attempt_lease_and_activation_are_fenced_and_update_execution(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    clock = Clock()
    attempts = AttemptStore(execution_store, clock=clock)

    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        attempt_id="attempt_1",
    )
    assert leased.status is AttemptStatus.LEASED
    assert leased.generation == 1
    assert reserved.status is ExecutionStatus.QUEUED
    assert reserved.current_attempt_id == leased.attempt_id
    assert reserved.owner_lease["generation"] == 1

    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    assert active.status is AttemptStatus.ACTIVE
    assert running.status is ExecutionStatus.RUNNING
    assert running.current_attempt_id == active.attempt_id


def test_a_stale_generation_is_refused(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    attempts = AttemptStore(execution_store, clock=Clock())
    leased, _ = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
    )
    with pytest.raises(AttemptConflict) as stale:
        attempts.ensure_held(leased.attempt_id, generation=leased.generation + 1)
    assert stale.value.code == "stale_generation"


def test_finish_attempt_changes_execution_and_clears_owner_lease_atomically(
    tmp_path,
) -> None:
    execution_store, execution = _execution(tmp_path)
    attempts = AttemptStore(execution_store, clock=Clock())
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )

    ended, completed = attempts.finish(
        active.attempt_id,
        generation=active.generation,
        expected_execution_version=running.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="result_committed",
    )
    assert ended.status is AttemptStatus.ENDED
    assert ended.outcome == "result_committed"
    assert completed.status is ExecutionStatus.COMPLETED
    assert completed.current_attempt_id is None
    assert completed.owner_lease == {}

    with pytest.raises(AttemptConflict) as terminal:
        attempts.ensure_held(
            ended.attempt_id,
            generation=ended.generation,
        )
    assert terminal.value.code == "terminal"

    with pytest.raises(AttemptConflict) as repeated_finish:
        attempts.finish(
            ended.attempt_id,
            generation=ended.generation,
            expected_execution_version=completed.status_version,
            target=ExecutionStatus.COMPLETED,
            outcome="result_committed",
        )
    assert repeated_finish.value.code == "terminal"


def _ended_with_failure(attempts, execution, failure_text):
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version, owner_id="worker_1",
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation, expected_execution_version=reserved.status_version,
    )
    return attempts.finish(
        active.attempt_id,
        generation=active.generation,
        expected_execution_version=running.status_version,
        target=ExecutionStatus.FAILED,
        outcome="failed",
        reason_code="agent_runner_error",
        failure_text=failure_text,
    )


def test_a_failed_attempt_keeps_its_readable_failure_out_of_events(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    attempts = AttemptStore(execution_store)

    ended, _ = _ended_with_failure(attempts, execution, "RuntimeError: the provider dropped the stream")

    assert ended.failure_text == "RuntimeError: the provider dropped the stream"
    assert "failure_text" not in ended.to_dict()
    assert [item.failure_text for item in attempts.list_for_execution(execution.execution_id)] == [ended.failure_text]
    assert not any("provider dropped" in json.dumps(item.payload, default=str)
                   for item in execution_store.list_events(execution.execution_id))


def test_failure_text_is_scrubbed_then_bounded(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    attempts = AttemptStore(execution_store)
    secret = "sk-" + "a1b2c3d4" * 8

    ended, _ = _ended_with_failure(attempts, execution, f"401 for key {secret} " + "x" * 9000)

    assert secret not in ended.failure_text
    assert len(ended.failure_text) == 4000


@pytest.mark.parametrize("token", [
    "QUJD" * 25_000,
    "0123456789abcdef" * 6_000,
    "a" * 100_000,
    "key" * 30_000,
    "http://" + "a:" * 40_000,
    "a.b-" * 25_000,
])
def test_scrubbing_a_long_unbroken_token_stays_quick(token: str) -> None:
    started = time.perf_counter()
    stored = stored_failure_text(f"BadRequest: invalid image data: {token}")
    assert time.perf_counter() - started < 0.5
    assert len(stored) == FAILURE_TEXT_CHARS


@pytest.mark.parametrize("text, secret", [
    ("could not connect to postgres://admin:s3cretpw@db.internal:5432/app", "s3cretpw"),
    ("login failed password=hunter2", "hunter2"),
    ('auth refused: {"password": "two words"}', "two words"),
    ("db_passwd: pw1", "pw1"),
    ("cookie: sessionid=abcdefghijklmnop; other=1", "abcdefghijklmnop"),
    ("Set-Cookie: sid=short", "sid=short"),
])
def test_failure_text_loses_the_credentials_errors_quote(text: str, secret: str) -> None:
    stored = stored_failure_text(text)
    assert secret not in stored
    assert "[secret removed]" in stored


def test_failure_text_keeps_the_words_around_a_scrubbed_value() -> None:
    assert stored_failure_text("could not connect to postgres://admin:s3cretpw@db.internal:5432/app") == (
        "could not connect to postgres://[secret removed]@db.internal:5432/app")
    assert stored_failure_text("openai_api_key: abcdefghijkl was refused") == (
        "openai_api_key: [secret removed] was refused")
    assert stored_failure_text("RuntimeError: the provider dropped the stream") == (
        "RuntimeError: the provider dropped the stream")


def test_blank_failure_text_stores_nothing(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    ended, _ = _ended_with_failure(AttemptStore(execution_store), execution, "  \n")
    assert ended.failure_text is None


def test_failure_text_survives_a_restart_and_older_rows_read_none(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    ended, _ = _ended_with_failure(AttemptStore(execution_store), execution, "ValueError: broke")

    reopened = ExecutionStore(tmp_path / "executions.db")
    reopened.get_execution(execution.execution_id)
    assert AttemptStore(reopened).get(ended.attempt_id).failure_text == "ValueError: broke"

    row = {"attempt_id": "older", "execution_id": execution.execution_id, "generation": 9, "status": "ended",
           "owner_id": "worker_0", "leased_at": 1.0, "updated_at": 1.0, "outcome": "failed", "fenced": 0,
           "written_by_a_later_build": "ignored"}
    reopened.tables._apply({"t": "attempts", "r": row})
    assert AttemptStore(reopened).get("older").failure_text is None


def test_attempts_of_an_execution_come_oldest_generation_first(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    attempts = AttemptStore(execution_store)
    ended, failed = _ended_with_failure(attempts, execution, None)
    assert [item.attempt_id for item in attempts.list_for_execution(execution.execution_id)] == [ended.attempt_id]
    assert failed.status is ExecutionStatus.FAILED
    assert attempts.list_for_execution("no-such-execution") == []
