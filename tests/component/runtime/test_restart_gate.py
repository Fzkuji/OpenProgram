"""What may hold back continuing a paused turn after a restart, and who is left to continue it for."""
from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update.control import maintenance
from openprogram.runtime import execution as execution_package
from openprogram.runtime.execution import restart
from openprogram.runtime.execution.agent_journal import ToolStep, interrupted_outcome
from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.control import RuntimeControlService
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.model import CapabilitySet, ExecutionStatus, _json
from openprogram.runtime.execution.store import ExecutionStore
from openprogram.runtime.jobs.input import JobAgentInputV1
from openprogram.runtime.jobs.runner.service import JobRunner
from openprogram.runtime.jobs.types import Job, JobStatus

SESSION = "local_s1"
ENTRYPOINT = "openprogram.runtime.conversation.production_driver:AgentProductionDriver"
CAPABILITIES = CapabilitySet(pause=True, safe_point_kinds=("action.after",), state_schema_version=1)


class World:
    def __init__(self, root, monkeypatch):
        self.store = ExecutionStore(root / "execution.sqlite3")
        self.attempts = AttemptStore(self.store)
        self.service = RuntimeControlService(self.store, self.attempts, DriverRegistry())
        self.jobs: dict[str, Job] = {}
        self.continued: list[str] = []
        self.count = 0
        monkeypatch.setattr(restart, "window_seconds", lambda: -1)
        monkeypatch.setattr(maintenance, "maintenance_blocks", lambda *_: False)
        monkeypatch.setattr(execution_package, "default_control_service", lambda: self.service)

        async def request_continue(**arguments):
            self.continued.append(arguments["execution_id"])

        monkeypatch.setattr(self.service, "request_continue", request_continue)
        self.runner = SimpleNamespace(
            _execution_store=self.store, _restart_shutdown=False, _execution_control=self.service,
            get_job=self._read_job,
            queue_job_resume=lambda **arguments: self.continued.append(arguments["execution_id"]),
        )

    def _read_job(self, job_id):
        # A Job's record sits behind the session lock, which admission takes
        # before this store's own: taking them the other way round can hang.
        assert not self.store.tables._in_transaction
        return self.jobs.get(job_id)

    def _name(self) -> str:
        self.count += 1
        return f"run{self.count}"

    def admit(self, *, parent=None, job_payload=None, execution_id=None):
        name = execution_id or self._name()
        revision = self.store.create_revision(manifest={"entrypoint": "chat"})
        chat = None if job_payload else {
            "version": 1, "kind": "chat", "request": {"user_text": name, "agent_id": "main", "source": "web"}}
        digest = hashlib.sha256(_json(dict(job_payload or chat)).encode("utf-8")).hexdigest()
        return self.store.admit_execution(
            session_id=SESSION, revision_id=revision.revision_id, input_ref=f"agent-turn:{digest}", input_hash=digest,
            entrypoint=ENTRYPOINT, trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
            execution_id=name, user_message_id=name, assistant_message_id=f"{name}_reply",
            parent_execution_id=parent, capabilities=CAPABILITIES, agent_turn_payload=chat, job_agent_payload=job_payload,
        )

    def admit_job(self, *, parent, wait=True):
        name = self._name()
        job = Job(id=name, parent_session_id=SESSION, prompt="part of the work", agent_id="main",
                  wait=wait, status=JobStatus.RUNNING)
        payload = JobAgentInputV1.from_job(job, run_id=None, parent_execution_id=parent).to_dict()
        self.jobs[name] = job
        return self.admit(parent=parent, job_payload=payload, execution_id=name)

    def admit_branch(self, source):
        """A Retry or Create branch: it starts from a saved point of ``source``."""
        branch = self.admit(parent=source)
        with self.store._transaction() as connection:
            connection.update("executions", {"source_checkpoint_id": "saved_point"}, execution_id=branch.execution_id)
        return self.store.get_execution(branch.execution_id)

    def run(self, execution, *, owned=False):
        leased, reserved = self.attempts.lease(
            execution.execution_id, expected_version=execution.status_version, owner_id="worker")
        active, running = self.attempts.activate(
            leased.attempt_id, generation=leased.generation, expected_execution_version=reserved.status_version)
        if owned:
            self.attempts.set_process_owner(active.attempt_id, generation=active.generation, active=True)
        return active

    def lose_owner(self, active):
        self.service.recover_owner_loss(active.execution_id, only_if_abandoned=True)
        paused = self.store.get_execution(active.execution_id)
        assert paused.status is ExecutionStatus.PAUSED
        return paused

    def move(self, execution_id, *targets, **fields):
        for target in targets:
            current = self.store.get_execution(execution_id)
            self.store.transition_execution(
                execution_id, expected_version=current.status_version, target=target, reason_code="test", **fields)

    def end(self, execution_id):
        self.move(execution_id, ExecutionStatus.FAILED)

    def status(self, execution_id):
        return self.store.get_execution(execution_id).status

    def reconcile(self):
        restart.reconcile(self.runner)

    def settled_as(self, execution_id):
        return [event.payload["outcome"] for event in self.store.list_events(execution_id)
                if event.kind == restart._SETTLED]


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _paused_chat(world, *, parent=None):
    return world.lose_owner(world.run(world.admit(parent=parent)))


def test_a_running_parent_does_not_hold_back_its_own_child(world):
    parent = world.admit()
    world.run(parent, owned=True)
    child = _paused_chat(world, parent=parent.execution_id)
    world.reconcile()
    assert world.continued == [child.execution_id]


def test_nor_does_a_running_grandparent(world):
    top = world.admit()
    world.run(top, owned=True)
    middle = world.admit(parent=top.execution_id)
    world.move(middle.execution_id, ExecutionStatus.PAUSED)
    child = _paused_chat(world, parent=middle.execution_id)
    world.reconcile()
    assert world.continued == [child.execution_id]


@pytest.mark.parametrize("stopping", [ExecutionStatus.PAUSING, ExecutionStatus.CANCELLING])
def test_a_turn_stopping_with_no_owner_left_does_not_block(world, stopping):
    sibling = world.admit()
    world.run(sibling)
    world.move(sibling.execution_id, stopping)
    paused = _paused_chat(world)
    world.reconcile()
    assert world.continued == [paused.execution_id]


@pytest.mark.parametrize("stopping", [ExecutionStatus.PAUSING, ExecutionStatus.CANCELLING])
def test_a_turn_stopping_under_a_live_owner_still_blocks(world, stopping):
    sibling = world.admit()
    world.run(sibling, owned=True)
    world.move(sibling.execution_id, stopping)
    _paused_chat(world)
    world.reconcile()
    assert world.continued == []


def test_a_queued_sibling_blocks(world):
    world.admit()
    _paused_chat(world)
    world.reconcile()
    assert world.continued == []


def test_a_running_sibling_with_no_owner_recorded_still_blocks(world):
    world.run(world.admit())
    _paused_chat(world)
    world.reconcile()
    assert world.continued == []


def test_a_branch_still_waits_for_the_running_turn_it_started_from(world):
    source = world.admit()
    world.run(source, owned=True)
    world.lose_owner(world.run(world.admit_branch(source.execution_id)))
    world.reconcile()
    assert world.continued == []


def test_a_resumed_parent_does_not_go_back_to_waiting(world):
    """Settles whether a resumed parent waits on a paused child: its call comes back as interrupted."""
    assert interrupted_outcome(ToolStep(tool_call_id="call", tool_name="agent", started={})) == "report"


def test_a_parent_still_waiting_hears_at_once_that_its_child_is_paused(world):
    parent = world.admit()
    world.run(parent, owned=True)
    child = world.admit_job(parent=parent.execution_id)
    world.lose_owner(world.run(child))
    job_runner = SimpleNamespace(
        get_job=world.jobs.get,
        stopped_execution=lambda job_id: JobRunner.stopped_execution(
            SimpleNamespace(_execution_store=world.store), job_id),
    )
    started = time.monotonic()
    heard = JobRunner.await_job_durable(job_runner, child.execution_id)
    assert heard is world.jobs[child.execution_id]
    assert time.monotonic() - started < 5


def test_a_waited_on_child_of_a_dead_parent_ends(world):
    parent = world.admit()
    child = world.admit_job(parent=parent.execution_id)
    world.lose_owner(world.run(child))
    world.end(parent.execution_id)
    world.reconcile()
    assert world.status(child.execution_id) is ExecutionStatus.INTERRUPTED
    assert world.settled_as(child.execution_id) == ["nobody_waiting"]
    assert world.continued == []


def test_a_waited_on_child_ends_when_its_intent_was_lost_too(world):
    parent = world.admit()
    child = world.admit_job(parent=parent.execution_id)
    world.lose_owner(world.run(child))
    world.end(parent.execution_id)
    with world.store._transaction() as connection:
        connection.delete("execution_events", execution_id=child.execution_id, kind=restart._REQUEST)
    world.reconcile()
    assert world.status(child.execution_id) is ExecutionStatus.INTERRUPTED


def test_a_waited_on_child_goes_on_while_a_run_above_it_is_left(world):
    top = world.admit()
    world.move(top.execution_id, ExecutionStatus.PAUSED)
    parent = world.admit(parent=top.execution_id)
    child = world.admit_job(parent=parent.execution_id)
    world.lose_owner(world.run(child))
    world.end(parent.execution_id)
    world.reconcile()
    assert world.continued == [child.execution_id]
    assert world.status(child.execution_id) is ExecutionStatus.PAUSED


def test_a_background_child_of_a_dead_parent_goes_on(world):
    parent = world.admit()
    child = world.admit_job(parent=parent.execution_id, wait=False)
    world.lose_owner(world.run(child))
    world.end(parent.execution_id)
    world.reconcile()
    assert world.continued == [child.execution_id]


def test_a_waited_on_child_of_a_dead_branch_ends_though_the_branch_source_is_paused(world):
    source = world.admit()
    world.move(source.execution_id, ExecutionStatus.PAUSED)
    branch = world.admit_branch(source.execution_id)
    child = world.admit_job(parent=branch.execution_id)
    world.lose_owner(world.run(child))
    world.end(branch.execution_id)
    world.reconcile()
    assert world.status(child.execution_id) is ExecutionStatus.INTERRUPTED


def test_a_chat_turn_forked_from_a_finished_turn_is_not_ended(world):
    parent = world.admit()
    world.end(parent.execution_id)
    child = world.lose_owner(world.run(world.admit_branch(parent.execution_id)))
    world.reconcile()
    assert world.continued == [child.execution_id]
    assert world.status(child.execution_id) is ExecutionStatus.PAUSED
