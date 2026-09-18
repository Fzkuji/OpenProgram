"""Auto refusals share durable execution history and canonical approval waits."""
import asyncio

import pytest

from openprogram.agent.authority import local_owner_authority
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.permissions.approval import wrap_with_approval
from openprogram.agent.run_control import set_current_execution_id, reset_current_execution_id
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.execution import ExecutionStore
from tests.component.agent.test_production_driver._support import _admitted


@pytest.fixture
def automatic(tmp_path, monkeypatch):
    store, execution = _admitted(tmp_path)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.programs.permission_rule.load_merged_rules", lambda _: None)
    request = TurnRequest(session_id=execution.session_id, user_text="inspect project", agent_id="main",
        source="web", permission_mode="auto", **local_owner_authority())
    token = set_current_execution_id(execution.execution_id)
    try:
        yield store, execution, request
    finally:
        reset_current_execution_id(token)


def test_third_refusal_uses_existing_approval_manifest(automatic, monkeypatch):
    _, _, request = automatic
    calls = []

    async def classify(*args, **kwargs):
        return True, "risky"

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[], details={})

    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    tool = wrap_with_approval(AgentTool(name="bash", label="bash", description="shell", parameters={}, execute=execute), request, lambda _: None)

    async def run():
        for index in range(3):
            args = {"command": f"command-{index}"}
            await tool._permission_preflight(str(index), args)
            manifest = tool._interaction_manifest(str(index), args)
            if index < 2:
                assert manifest is None
                assert (await tool.execute(str(index), args, None, None)).is_error
            else:
                assert manifest["kind"] == "approval"
                assert manifest["request_metadata"]["approval_reason"] == "AUTO_REFUSAL_FALLBACK"
                assert manifest["request_metadata"]["args"] == args
    asyncio.run(run())
    assert not calls


def _tool(request, monkeypatch, classify):
    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    async def execute(*_):
        return AgentToolResult(content=[], details={})
    return wrap_with_approval(AgentTool(name="bash", label="bash", description="shell", parameters={}, execute=execute), request, lambda _: None)


def _reviews(store, execution):
    return [event.payload for event in store.list_events(execution.execution_id) if event.kind == "permission.auto.reviewed"]


def test_review_reuses_durable_verdict_after_store_reopen(automatic, monkeypatch):
    store, execution, request = automatic
    calls = []
    async def classify(*args, **kwargs):
        calls.append(args)
        return True, "risky"
    async def run():
        tool = _tool(request, monkeypatch, classify)
        for i in range(3):
            await tool._permission_preflight(str(i), {"command": str(i)})
        monkeypatch.setattr("openprogram.execution.default_store", lambda: ExecutionStore(store.path))
        restored = _tool(request, monkeypatch, classify)
        await restored._permission_preflight("2", {"command": "2"})
        assert restored._interaction_manifest("2", {"command": "2"})["kind"] == "approval"
    asyncio.run(run())
    assert len(calls) == 3
    assert len(_reviews(store, execution)) == 3
    assert _reviews(store, execution)[-1]["total"] == 3


def test_allowed_resets_consecutive_but_twenty_total_still_falls_back(automatic, monkeypatch):
    store, execution, request = automatic
    async def classify(_name, args, **kwargs):
        return args["command"].startswith("risk"), "classified"
    async def run():
        tool = _tool(request, monkeypatch, classify)
        for i in range(20):
            await tool._permission_preflight(f"safe-{i}", {"command": "safe"})
            args = {"command": f"risk-{i}"}
            await tool._permission_preflight(str(i), args)
            manifest = tool._interaction_manifest(str(i), args)
            assert (manifest is not None) is (i == 19)
    asyncio.run(run())
    assert _reviews(store, execution)[-1]["consecutive"] == 1
    assert _reviews(store, execution)[-1]["total"] == 20


def test_no_verdict_is_not_a_risk_refusal(automatic, monkeypatch):
    from openprogram.agent.permissions.classifier import ReviewResult
    store, execution, request = automatic
    async def classify(*args, **kwargs):
        return ReviewResult(True, "provider unavailable", "AUTO_CLASSIFIER_UNAVAILABLE")
    async def run():
        tool = _tool(request, monkeypatch, classify)
        for i in range(4):
            args = {"command": str(i)}
            await tool._permission_preflight(str(i), args)
            assert tool._interaction_manifest(str(i), args) is None
            result = await tool.execute(str(i), args, None, None)
            assert result.details["reason_code"] == "AUTO_CLASSIFIER_UNAVAILABLE"
            assert result.details["outcome"] == "not_started"
    asyncio.run(run())
    assert _reviews(store, execution) == []


@pytest.mark.parametrize("restriction", ["noninteractive", "explicit_deny"])
def test_fallback_never_overrides_authority_or_explicit_denial(automatic, monkeypatch, restriction):
    from openprogram.agent.session_config import PermissionRules
    _, _, request = automatic
    async def classify(*args, **kwargs):
        return True, "risky"
    async def run():
        tool = _tool(request, monkeypatch, classify)
        for i in range(3):
            await tool._permission_preflight(str(i), {"command": str(i)})
        if restriction == "noninteractive":
            request.source = "agent_spawn"
            request.interaction = "noninteractive"
        else:
            monkeypatch.setattr("openprogram.programs.permission_rule.load_merged_rules", lambda _: PermissionRules(deny=["bash"]))
        restored = _tool(request, monkeypatch, classify)
        args = {"command": "2"}
        await restored._permission_preflight("2", args)
        assert restored._interaction_manifest("2", args) is None
        assert (await restored.execute("2", args, None, None)).is_error
    asyncio.run(run())


def test_real_driver_publishes_fallback_before_tool_dispatch(tmp_path, monkeypatch):
    from openprogram.agent.agent_loop import _execute_tool_calls
    from openprogram.execution.effects import EffectStore
    from openprogram.execution.waits import DurableWaitStore
    from openprogram.providers.types import AssistantMessage, ToolCall
    from openprogram.providers.utils.event_stream import EventStream
    from tests.component.agent.test_production_driver._support import _prepare_long_turn, _long_turn_snapshot
    store, _, _, _, request, _, execution, _, hook = _prepare_long_turn(tmp_path, "fallback-wait")
    request.permission_mode = "auto"
    request.source = "web"
    for key, value in local_owner_authority().items():
        setattr(request, key, value)
    snapshot = _long_turn_snapshot(request)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.programs.permission_rule.load_merged_rules", lambda _: None)
    calls = []
    async def classify(*args, **kwargs):
        return True, "risky"
    async def executor(*args):
        calls.append(args)
        return AgentToolResult(content=[], details={})
    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    tool = wrap_with_approval(AgentTool(name="bash", label="bash", description="shell", parameters={}, execute=executor), request, lambda _: None)
    async def safe_point(kind, payload):
        return hook(kind, payload)
    async def run():
        for i in range(3):
            message = AssistantMessage(content=[ToolCall(id=str(i), name="bash", arguments={"command": str(i)})],
                api="fake", provider="fake", model="fake", timestamp=i, stop_reason="toolUse")
            hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": [str(i)]}})
            hook("provider.after", {"message": message.model_dump(mode="json"), "tool_call_ids": [str(i)], "next_tool_index": 0})
            outcome = await _execute_tool_calls([tool], message, None, EventStream(), None, {}, safe_point_hook=safe_point)
            if i == 2:
                assert outcome["tool_results"] == []
    token = set_current_execution_id(execution.execution_id)
    try:
        asyncio.run(run())
    finally:
        reset_current_execution_id(token)
    waits = DurableWaitStore(ExecutionStore(store.path)).list_open(session_id=request.session_id)
    assert len(waits) == 1
    assert waits[0].request["approval_reason"] == "AUTO_REFUSAL_FALLBACK"
    assert waits[0].request["args"] == {"command": "2"}
    assert not calls
    assert EffectStore(store).list_unresolved(execution.execution_id) == []


def test_parallel_review_updates_are_deduplicated(automatic):
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.agent.permissions.auto_history import AutoHistory
    store, execution, _ = automatic
    def record(i):
        history = AutoHistory(store, execution.execution_id, execution.session_id, "epoch", str(i % 5))
        return history.record(blocked=True, reason="unsafe", code="AUTO_CLASSIFIER_DENY")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(20)))
    reviews = _reviews(store, execution)
    assert len(reviews) == 5
    assert reviews[-1]["total"] == reviews[-1]["consecutive"] == 5


def test_fallback_consumes_durable_exact_owner_answer(automatic, monkeypatch):
    import time
    from openprogram.execution import AttemptStore
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.waits import DurableWaitStore
    from openprogram.agent.run_control import set_preapproved_wait_id, reset_preapproved_wait_id
    store, execution, request = automatic
    calls = []
    async def classify(*args, **kwargs):
        return True, "risky"
    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[], details={})
    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    raw = AgentTool(name="bash", label="bash", description="shell", parameters={}, execute=execute)
    tool = wrap_with_approval(raw, request, lambda _: None)
    async def prepare():
        for i in range(3):
            await tool._permission_preflight(str(i), {"command": str(i)})
        return tool._interaction_manifest("2", {"command": "2"})
    manifest = asyncio.run(prepare())
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id, expected_version=execution.status_version,
        owner_id="test", ttl_seconds=30)
    active, running = attempts.activate(leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version)
    wait = DurableWaitStore(store).open_wait(wait_id="auto-approval", execution_id=execution.execution_id,
        attempt_id=active.attempt_id, generation=active.generation, kind="approval",
        request={"prompt": manifest["prompt"], "options": manifest["options"], "multi": False,
                 "allow_custom": False, "detail": manifest["detail"], "schema": {}, "questions": [],
                 **manifest["request_metadata"]}, policy_snapshot=manifest["policy_snapshot"], expires_at=time.time() + 60)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    asyncio.run(service.request_wait_answer(command_id="answer-auto", execution_id=execution.execution_id,
        expected_version=store.get_execution(execution.execution_id).status_version, actor=local_owner_authority(),
        wait_id=wait.wait_id, generation=wait.claim_generation, answer={"answer": "approve", "scope": "once"}))
    monkeypatch.setattr("openprogram.execution.default_store", lambda: ExecutionStore(store.path))
    restored = wrap_with_approval(raw, request, lambda _: None)
    token = set_preapproved_wait_id(wait.wait_id)
    try:
        result = asyncio.run(restored.execute("2", {"command": "2"}, None, None))
    finally:
        reset_preapproved_wait_id(token)
    assert not result.is_error
    assert len(calls) == 1
    assert _reviews(store, execution)[-1]["total"] == 3
    assert _reviews(store, execution)[-1]["consecutive"] == 0


def test_changed_file_during_review_does_not_execute_or_count(automatic, monkeypatch, tmp_path):
    store, execution, request = automatic
    target = tmp_path / "review.txt"
    target.write_text("before")
    calls = []
    async def classify(*args, **kwargs):
        target.write_text("changed by owner")
        return False, "safe"
    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[], details={})
    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    tool = wrap_with_approval(AgentTool(name="write", label="write", description="file", parameters={}, execute=execute), request, lambda _: None)
    result = asyncio.run(tool.execute("file", {"file_path": str(target), "content": "overwrite"}, None, None))
    assert result.details["reason_code"] == "PERMISSION_REVIEW_STALE"
    assert target.read_text() == "changed by owner"
    assert not calls
    assert _reviews(store, execution) == []


def test_cancelled_review_has_no_durable_verdict(automatic, monkeypatch):
    store, execution, request = automatic
    async def classify(*args, **kwargs):
        raise asyncio.CancelledError
    tool = _tool(request, monkeypatch, classify)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tool._permission_preflight("cancel", {"command": "test"}))
    assert _reviews(store, execution) == []


def test_changed_working_directory_invalidates_review(automatic, monkeypatch, tmp_path):
    from openprogram.worktree.context import set_worktree, reset_worktree
    store, execution, request = automatic
    calls = []
    async def classify(*args, **kwargs):
        assert kwargs["context"]["working_directory"] == str(tmp_path / "first")
        set_worktree(str(tmp_path / "second"))
        return False, "safe"
    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[], details={})
    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    tool = wrap_with_approval(AgentTool(name="bash", label="bash", description="shell", parameters={}, execute=execute), request, lambda _: None)
    async def run():
        token = set_worktree(str(tmp_path / "first"))
        try:
            return await tool.execute("cwd", {"command": "touch output"}, None, None)
        finally:
            reset_worktree(token)
    result = asyncio.run(run())
    assert result.details["reason_code"] == "PERMISSION_REVIEW_STALE"
    assert not calls
    assert _reviews(store, execution) == []


def test_identical_auto_refusals_reach_approval_through_real_loop(automatic, monkeypatch):
    from openprogram.agent.agent_loop import _execute_tool_calls
    from openprogram.providers.types import AssistantMessage, ToolCall
    from openprogram.providers.utils.event_stream import EventStream
    store, execution, request = automatic
    async def classify(*args, **kwargs):
        return True, "risky"
    tool = _tool(request, monkeypatch, classify)
    failures = {}
    manifests = []
    async def safe_point(kind, payload):
        if kind == "tool.before" and payload.get("pre_wait"):
            manifests.append(payload["pre_wait"])
            return True
        return False
    async def run():
        for index in range(3):
            message = AssistantMessage(content=[ToolCall(id=str(index), name="bash", arguments={"command":"same"})],
                api="fake", provider="fake", model="fake", timestamp=index, stop_reason="toolUse")
            await _execute_tool_calls([tool], message, None, EventStream(), None, failures, safe_point_hook=safe_point)
    asyncio.run(run())
    assert len(manifests) == 1
    assert manifests[0]["request_metadata"]["approval_reason"] == "AUTO_REFUSAL_FALLBACK"
    assert _reviews(store, execution)[-1]["consecutive"] == 3
    assert not failures


def test_total_threshold_approval_resets_total(automatic):
    from openprogram.agent.permissions.auto_history import AutoHistory
    store, execution, _ = automatic
    for index in range(20):
        history = AutoHistory(store, execution.execution_id, execution.session_id, "epoch", str(index))
        history.record(blocked=True, reason="risk", code="AUTO_CLASSIFIER_DENY")
    approved = history.record(blocked=False, reason="approved", code="AUTO_OWNER_APPROVED", approved=True)
    assert approved["total"] == approved["consecutive"] == 0
