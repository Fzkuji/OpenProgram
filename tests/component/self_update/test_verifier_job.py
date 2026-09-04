"""Verifier options cross the real durable Job and dispatcher boundaries."""
from __future__ import annotations

import json

import pytest

from tests.component.agent.async_job_support import store_fixture  # noqa: F401


def _options():
    return dict(
        source="self_update_verify",
        profile_snapshot={"id": "main", "system_prompt": "Frozen verifier", "tools": ["read"]},
        model_override="provider/frozen-model", tools_override=["read"],
        response_format={"type": "json_schema", "name": "acceptance", "schema": {"type": "object"}},
        context_mode="clean", spawn_caller="a1", advance_head=False,
    )


@pytest.fixture
def runner(store_fixture, monkeypatch):
    from openprogram.agent.job.runner import JobRunner
    monkeypatch.setattr("openprogram.agent.job.runner._broadcast", lambda *a, **k: None)
    instance = JobRunner(max_workers=1)
    yield instance
    instance.shutdown()


def test_job_snapshot_survives_json_reload_without_caller_aliases(runner):
    from openprogram.agent.job.store import load_job
    from openprogram.agent.job.types import Job
    options = _options()
    job_id = runner.spawn_job("p1", "verify", "main", defer_dispatch=True, **options)
    options["profile_snapshot"]["tools"].append("bash")
    options["response_format"]["schema"]["type"] = "string"
    options["tools_override"].append("bash")
    stored = load_job("p1", job_id)
    assert stored.profile_snapshot["tools"] == ["read"]
    assert stored.tools_override == ["read"]
    assert stored.response_format["schema"]["type"] == "object"
    payload = stored.to_dict()
    restored = Job.from_dict(json.loads(json.dumps(payload)))
    payload["profile_snapshot"]["tools"].append("write")
    assert restored.profile_snapshot["tools"] == ["read"]
    assert restored.source == "self_update_verify"
    legacy = Job.from_dict({"id": "legacy", "parent_session_id": "p1", "prompt": "x", "agent_id": "main"})
    assert legacy.source == "agent_spawn"
    assert legacy.profile_snapshot is None


@pytest.mark.parametrize("borrowed", [False, True])
def test_normal_and_borrowed_jobs_preserve_verifier_request(runner, store_fixture, monkeypatch, borrowed):
    from openprogram.agent import dispatcher
    from openprogram.agent.job.types import JobStatus
    captured = []
    # This test isolates Job option transport. Startup authorization is covered
    # with real durable update/Job state in test_recovery.py.
    monkeypatch.setattr("openprogram.self_update.recovery.require_verifier_execution", lambda **_: None)

    def dispatch(req):
        if req.user_text == "parent":
            child = runner.spawn_job(
                "p1", "verify", "main",
                borrow_current_claim=True,
                **_options(),
            )
            result = runner.await_job(child, timeout=2)
            assert result.status is JobStatus.COMPLETED, result.error
            return dispatcher.TurnResult(result.result_text or "", "parent_u", "parent_a")
        captured.append(req)
        return dispatcher.TurnResult('{"ok":true}', "verify_u", "verify_a")

    monkeypatch.setattr(dispatcher, "process_user_turn", dispatch)
    store_fixture.update_session("p1", extra_meta={"provider_override": "wrong", "model_override": "live"})
    if borrowed:
        job_id = runner.spawn_job("p1", "parent", "main")
    else:
        job_id = runner.spawn_job("p1", "verify", "main", **_options())
    result = runner.await_job(job_id, timeout=5)
    assert result.status is JobStatus.COMPLETED, result.error
    assert len(captured) == 1
    req = captured[0]
    assert req.source == "self_update_verify"
    assert req.profile_snapshot["system_prompt"] == "Frozen verifier"
    assert req.response_format.name == "acceptance"
    assert req.model_override == "provider/frozen-model"
    assert req.history_override == [] and req.branch_from is None
    assert req.spawn_caller == "a1" and req.advance_head is False


@pytest.mark.parametrize("missing", ["profile_snapshot", "model_override", "tools_override", "response_format", "spawn_caller"])
def test_verifier_does_not_fall_back_to_live_configuration(runner, monkeypatch, missing):
    from openprogram.agent.job.types import JobStatus
    options = _options()
    options.pop(missing)
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", lambda req: pytest.fail("unsafe dispatch"))
    job_id = runner.spawn_job("p1", "verify", "main", **options)
    result = runner.await_job(job_id, timeout=5)
    assert result.status is JobStatus.ERRORED
    assert "verifier requires" in result.error


def test_verifier_policy_cannot_be_expanded_by_explicit_tools():
    from openprogram.programs import agent_tools, apply_tool_policy
    tools = agent_tools(names=["read", "bash", "write", "agent", "send_message", "self_update_prepare"])
    assert "bash" in {tool.name for tool in tools}
    assert {tool.name for tool in apply_tool_policy(tools, source="self_update_verify", exposure_filter=False)} == {"read"}
    assert {tool.name for tool in agent_tools(names=[t.name for t in tools], source="self_update_verify")} == {"read"}


def test_verifier_has_a_canonical_live_observer_but_no_write_capability():
    from openprogram.programs import agent_tools
    names = {t.name for t in agent_tools(names=["self_update_observe", "bash", "write", "web_fetch"], source="self_update_verify")}
    assert names == {"self_update_observe"}


@pytest.mark.parametrize("overrides", [
    {"advance_head": True}, {"context_mode": "inherit", "parent_msg_id": "a1"},
])
def test_verifier_rejects_main_branch_context(runner, monkeypatch, overrides):
    from openprogram.agent.job.types import JobStatus
    options = {**_options(), **overrides}
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", lambda req: pytest.fail("unsafe dispatch"))
    job_id = runner.spawn_job("p1", "verify", "main", **options)
    result = runner.await_job(job_id, timeout=5)
    assert result.status is JobStatus.ERRORED
    assert "clean non-head branch" in result.error


def test_verifier_requires_provider_qualified_model(runner, monkeypatch):
    from openprogram.agent.job.types import JobStatus
    options = {**_options(), "model_override": "bare-model"}
    monkeypatch.setattr("openprogram.agent.dispatcher.process_user_turn", lambda req: pytest.fail("unsafe dispatch"))
    job_id = runner.spawn_job("p1", "verify", "main", **options)
    result = runner.await_job(job_id, timeout=5)
    assert result.status is JobStatus.ERRORED
    assert "verifier requires" in result.error


@pytest.mark.parametrize("name", ["read", "self_update_observe"])
def test_verifier_rejects_a_custom_executor_using_a_read_tool_name(name):
    from openprogram.programs import get_agent_tool, apply_tool_policy
    from openprogram.agent.internals._model_tools import resolve_tools
    original = get_agent_tool(name)

    async def custom_execute(*args, **kwargs):
        pytest.fail("custom executor must never be exposed")

    impostor = original.model_copy(update={"execute": custom_execute})
    assert apply_tool_policy([impostor], source="self_update_verify", exposure_filter=False) == []
    assert resolve_tools({}, [impostor], source="self_update_verify") == []
    assert apply_tool_policy([original], source="self_update_verify") == [original]


@pytest.mark.parametrize("name", ["read", "self_update_observe"])
def test_verifier_rejects_in_place_mutation_of_a_registered_read_tool(monkeypatch, name):
    from openprogram.programs import get_agent_tool, apply_tool_policy, agent_tools
    original = get_agent_tool(name)

    async def custom_execute(*args, **kwargs):
        pytest.fail("mutated executor must never be exposed")

    monkeypatch.setattr(original, "execute", custom_execute)
    assert apply_tool_policy([original], source="self_update_verify", exposure_filter=False) == []
    assert agent_tools(names=[name], source="self_update_verify") == []
