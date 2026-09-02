from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from openprogram.agent.job.input import JobAgentInputError, JobAgentInputV1
from openprogram.agent.production_driver import AgentProductionDriver, CanonicalAgentEntry
from openprogram.execution import ExecutionStore


def _payload(*, caller=True, admission_id="admission-1"):
    return {
        "version": 1,
        "kind": "job_agent",
        "turn_request": {
            "session_id": "session-1", "user_text": "run", "agent_id": "main",
            "source": "job", "spawn_caller": "node-1" if caller else None,
            "spawned_from_session": "session-0" if caller else None,
        },
        "job_context": {
            "parent_execution_id": "job-parent", "run_id": "run-1",
            "branch_frontier": None,
            "caller": {
                "execution_id": "job-parent", "session_id": "session-0",
                "msg_id": "message-1", "node_id": "node-1",
            } if caller else None,
            "worktree_id": "worktree-1", "authority_snapshot": {},
            "deferred_inbox": None, "chain": {"messages": 2, "generations": 1},
            "relation": "owned", "origin_turn_id": "message-1",
            "resource_hints": {
                "admission_id": admission_id, "budget_scope_id": "scope-1",
                "effective_limits": {"max_total_tokens": 10},
                "resolved_limits_snapshot": None, "caller_generations": 0,
            },
        },
    }


def test_job_input_rejects_job_keys_nested_in_turn_request_and_inconsistent_caller():
    invalid = _payload()
    invalid["turn_request"]["worktree_id"] = "wrong-place"
    with pytest.raises(JobAgentInputError):
        JobAgentInputV1.parse(invalid)

    invalid = _payload()
    invalid["turn_request"]["spawn_caller"] = "other-node"
    with pytest.raises(JobAgentInputError):
        JobAgentInputV1.parse(invalid)


def test_job_input_caps_chain_and_resource_numbers():
    invalid = _payload()
    invalid["job_context"]["chain"]["messages"] = 100_001
    with pytest.raises(JobAgentInputError):
        JobAgentInputV1.parse(invalid)

    invalid = _payload()
    invalid["job_context"]["resource_hints"]["effective_limits"] = {"cap": 10**16}
    with pytest.raises(JobAgentInputError):
        JobAgentInputV1.parse(invalid)


def test_existing_job_activation_reads_the_outer_input_without_minting_identity(tmp_path):
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    payload = JobAgentInputV1.parse(_payload()).to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    revision = store.create_revision(manifest={"entrypoint": "job"})
    store.admit_execution(
        execution_id="j_existing", run_id="run-1", session_id="session-1",
        revision_id=revision.revision_id, input_ref=f"job-input-v1:{encoded}",
        input_hash=digest, entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "owner"}, config_snapshot_ref="config:job",
        capabilities=AgentProductionDriver.capabilities_for_payload({
            "version": 1, "kind": "chat", "request": payload["turn_request"],
        }), job_agent_payload=payload,
    )
    seen = {}

    def run_turn(*, request, cancel_event, execution_context):
        seen["request"] = request
        seen["context"] = execution_context
        return SimpleNamespace(failed=False, error=None)

    driver = AgentProductionDriver(store, turn_runner=run_turn)
    resolved = driver.resolve_existing_job("j_existing")
    assert resolved.request.user_text == "run"
    assert resolved.job_context["worktree_id"] == "worktree-1"

    entry = CanonicalAgentEntry(store, driver)

    async def activate():
        active = await entry.activate_existing_job("j_existing", "admission-1", 1)
        assert active is not None and active.admission.execution_id == "j_existing"
        handle = driver._handles[("j_existing", active.attempt_id, active.generation)]
        await handle.done

    asyncio.run(activate())
    assert seen["request"].session_id == "session-1"
    assert seen["context"]["job_context"]["caller"]["node_id"] == "node-1"
    assert store.get_execution("j_existing").execution_id == "j_existing"
