"""An earlier step with an unknown outcome is reported to the model and blocks nothing."""
import asyncio

import pytest

from openprogram.runtime.permissions.authority import local_owner_authority
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.permissions.approval import wrap_with_approval
from openprogram.runtime.permissions.modes import PermissionRules
from openprogram.runtime.permissions.policy import permission_decision
from openprogram.runtime.permissions import recovery as lookup
from openprogram.models.providers.types import AgentTool, AgentToolResult, TextContent
from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.control import RuntimeControlService
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.effects import EffectClassification, EffectStatus, EffectStore
from openprogram.runtime.execution.store import ExecutionStore


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _: None)
    lookup._noticed.clear()
    return store


def orphan(store, *, session="recovery", name=None, parent=None, kind="tool.before", caller=None):
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    if caller:
        import hashlib
        import json
        from openprogram.runtime.jobs.input import JobAgentInputV1
        from openprogram.runtime.jobs.types import Job

        job = Job(id="called-job", parent_session_id=session, prompt="inspect", agent_id="main",
                  caller_session_id=caller, caller_msg_id="caller-message")
        payload = JobAgentInputV1.from_job(job).to_dict()
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        execution = store.admit_execution(session_id=session, revision_id=revision.revision_id,
            input_ref="job-input-v1:" + serialized,
            input_hash=hashlib.sha256(serialized.encode()).hexdigest(),
            entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
            trusted_actor=local_owner_authority(), config_snapshot_ref="config:test",
            job_agent_payload=payload)
    else:
        execution = store.create_execution(
            session_id=session, revision_id=revision.revision_id,
            parent_execution_id=parent,
        )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="old-worker",
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    effects = EffectStore(store)
    effect = effects.register(
        effect_id=f"effect:{execution.execution_id}", execution_id=execution.execution_id,
        attempt_id=active.attempt_id, action_id="old-call",
        classification=EffectClassification.NONREPEATABLE, idempotency_key=None,
        metadata={"kind": kind, "payload": {"tool_name": name, "tool_call_id": "old-call"}},
    )
    effects.mark_dispatched(effect.effect_id, expected_status=EffectStatus.PLANNED)
    RuntimeControlService(store, attempts, DriverRegistry()).recover_owner_loss(execution.execution_id)
    return execution, effect


def operation(name="bash", *, mode="bypass", source="web", session="recovery"):
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[], details={})

    tool = AgentTool(name=name, label=name, description="test", parameters={}, execute=execute)
    req = TurnRequest(session_id=session, user_text="continue", agent_id="main",
                      source=source, permission_mode=mode, **local_owner_authority())
    return tool, req, calls


def _texts(result) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def _run(tool, req, call_id="new-call"):
    return asyncio.run(wrap_with_approval(tool, req, lambda _: None).execute(call_id, {}, None, None))


@pytest.mark.parametrize("mode", ["bypass", "acceptEdits", "auto", "default"])
def test_an_unknown_outcome_does_not_change_the_decision(recovery, mode):
    tool, req, _ = operation("bash", mode=mode)
    before = permission_decision(tool, req, {})
    orphan(recovery)
    assert permission_decision(tool, req, {}) == before


def test_the_call_runs_and_its_result_carries_the_notice_once(recovery):
    _, effect = orphan(recovery, name="bash")
    tool, req, calls = operation("bash", mode="bypass")
    assert wrap_with_approval(tool, req, lambda _: None)._interaction_manifest("new-call", {}) is None
    first = _run(tool, req)
    assert len(calls) == 1 and not first.is_error
    assert "may or may not have finished" in _texts(first) and "bash (old-call)" in _texts(first)
    assert "may or may not have finished" not in _texts(_run(tool, req, "next-call"))
    assert len(calls) == 2
    assert EffectStore(recovery).get(effect.effect_id).status is EffectStatus.DISPATCHED


def test_the_notice_comes_before_the_tools_own_output(recovery):
    orphan(recovery, name="bash")
    tool, req, _ = operation("bash", mode="bypass")

    async def execute(*_args):
        return AgentToolResult(content=[TextContent(text='{"ok": true}')], details={})

    tool.execute = execute
    content = _run(tool, req).content
    assert content[0].text.startswith("[notice]") and content[1].text == '{"ok": true}'


@pytest.mark.parametrize("kind,name", [("gui.primitive", None), ("tool.before", "computer_use_agent")])
def test_an_interrupted_screen_step_is_not_reported(recovery, kind, name):
    # What the step changed is on the screen, and its report tells the model to look.
    _, effect = orphan(recovery, name=name, kind=kind)
    tool, req, _ = operation("bash", mode="bypass")
    assert [item["effect_id"] for item in lookup.uncertain_operations(req)] == [effect.effect_id]
    assert lookup.uncertain_notice(req, tool) is None


def test_a_screen_step_beside_another_unknown_outcome_names_only_that_one(recovery):
    orphan(recovery, name=None, kind="gui.primitive")
    orphan(recovery, name="bash")
    tool, req, _ = operation("write", mode="bypass")
    notice = lookup.uncertain_notice(req, tool)
    assert "bash" in notice and "computer_use_agent" not in notice


def test_lookup_follows_admissions_and_reads_no_unrelated_payload(recovery, monkeypatch):
    """It keeps a conversation's members until an execution is admitted, and
    reads payloads only of effects of this conversation."""
    from openprogram.runtime.execution import conversation_scope

    scoped = []
    original = conversation_scope.conversation_execution_scope

    def counted(store, session_id, **kwargs):
        scoped.append(session_id)
        return original(store, session_id, **kwargs)

    monkeypatch.setattr(conversation_scope, "conversation_execution_scope", counted)
    _, other = orphan(recovery, session="elsewhere", name="bash")
    tool, req, _ = operation()
    assert lookup.uncertain_operations(req) == []
    assert lookup.uncertain_operations(req) == []
    assert scoped == ["recovery"]
    assert not any(effect_id == other.effect_id for _path, effect_id in lookup._identities)
    _, mine = orphan(recovery, name="bash")
    assert [item["effect_id"] for item in lookup.uncertain_operations(req)] == [mine.effect_id]
    assert scoped == ["recovery", "recovery"]
    assert [item["tool"] for item in lookup.uncertain_operations(req)] == ["bash"]
    assert scoped == ["recovery", "recovery"]


def test_an_inspection_gets_no_notice_and_leaves_it_for_the_next_call(recovery, tmp_path):
    import copy
    from openprogram.programs.tools.files.read import read

    orphan(recovery)
    bash, req, _ = operation()
    assert lookup.uncertain_notice(req, read) is None
    assert lookup.uncertain_notice(req, copy.copy(read)) is None
    fake, _, _ = operation("read")
    copied = copy.copy(read)
    copied.execute = fake.execute
    assert lookup.uncertain_notice(req, copied) is not None
    lookup._noticed.clear()
    target = tmp_path / "inspect.txt"
    target.write_text("saved result", encoding="utf-8")
    result = asyncio.run(wrap_with_approval(read, req, lambda _: None).execute(
        "inspect", {"file_path": str(target)}, None, None))
    assert "saved result" in str(result.content) and "may or may not" not in str(result.content)
    assert lookup.uncertain_notice(req, bash) is not None


def test_conversation_descendants_but_not_unrelated_session(recovery):
    revision = recovery.create_revision(manifest={})
    parent = recovery.create_execution(session_id="recovery", revision_id=revision.revision_id)
    _, effect = orphan(recovery, parent=parent.execution_id)
    _, req, _ = operation()
    assert [item["effect_id"] for item in lookup.uncertain_operations(req)] == [effect.effect_id]
    req.session_id = "unrelated"
    assert lookup.uncertain_operations(req) == []
    req.spawned_from_session = "recovery"
    assert [item["effect_id"] for item in lookup.uncertain_operations(req)] == [effect.effect_id]


def test_provider_only_and_resolved_effects_are_not_reported(recovery):
    orphan(recovery, kind="provider.before")
    _, req, _ = operation()
    assert lookup.uncertain_operations(req) == []
    _, effect = orphan(recovery)
    effects = EffectStore(recovery)
    effects.resolve(effect.effect_id, expected_status=EffectStatus.DISPATCHED,
                    outcome=EffectStatus.COMMITTED, receipt={"result": "verified"})
    assert lookup.uncertain_operations(req) == []


def test_exact_called_job_not_its_entire_target_session(recovery):
    _, called = orphan(recovery, session="target", caller="recovery")
    orphan(recovery, session="target")
    _, req, _ = operation()
    assert {item["effect_id"] for item in lookup.uncertain_operations(req)} == {called.effect_id}


def test_unavailable_recovery_state_does_not_stop_the_call(recovery, monkeypatch):
    def unavailable():
        raise OSError("unavailable")

    monkeypatch.setattr("openprogram.runtime.execution.default_store", unavailable)
    tool, req, calls = operation()
    result = _run(tool, req)
    assert not result.is_error and len(calls) == 1


def test_a_deny_rule_still_decides(recovery, monkeypatch):
    orphan(recovery, name="bash")
    tool, req, _ = operation("bash", mode="bypass")
    req.permission_rules = PermissionRules(deny=["bash"])
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _: req.permission_rules)
    assert permission_decision(tool, req, {}).code == "RULE_DENY"


def admit_owner_turn(store, request):
    """Real immutable admission, also used by the isolated process probe."""
    import hashlib
    import json
    from openprogram.state.authority import normalize_authority

    payload = {"version": 1, "kind": "chat", "request": {
        "user_text": request.user_text, "agent_id": request.agent_id,
        "source": request.source,
    }}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    return store.admit_execution(
        session_id=request.session_id, revision_id=revision.revision_id,
        input_ref="agent-turn:test", input_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
        trusted_actor=normalize_authority(request), config_snapshot_ref="config:test",
        agent_turn_payload=payload,
    )
