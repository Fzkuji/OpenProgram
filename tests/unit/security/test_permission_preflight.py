"""Authorization precedes dispatch and stale reviews never authorize execution."""
import asyncio

import pytest

from openprogram.agent.agent_loop import _execute_tool_calls
from openprogram.agent.authority import owner_authority
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.permissions.approval import wrap_with_approval
from openprogram.agent.session_config import PermissionRules
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.providers.types import AssistantMessage, ToolCall
from openprogram.providers.utils.event_stream import EventStream


@pytest.mark.parametrize("blocked", [False, True])
def test_review_precedes_dispatch_and_controls_bash(monkeypatch, blocked):
    order = []
    monkeypatch.setattr("openprogram.programs.permission_rule.load_merged_rules", lambda _: None)

    async def classify(name, args, **kwargs):
        order.append("review")
        assert name == "bash" and args == {"command": "git status"}
        assert kwargs["context"]["user_request"] == "inspect changes"
        return blocked, "review result"

    async def execute(*args):
        order.append("execute")
        return AgentToolResult(content=[], details={})

    async def checkpoint(kind, payload):
        order.append(kind)
        return False

    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    request = TurnRequest(session_id="preflight", user_text="inspect changes", agent_id="main",
        source="web", permission_mode="auto", **owner_authority("owner/install/0123456789abcdef"))
    tool = wrap_with_approval(AgentTool(name="bash", label="bash", description="shell",
        parameters={}, execute=execute), request, lambda _: None)
    message = AssistantMessage(content=[ToolCall(id="c", name="bash", arguments={"command": "git status"})],
        api="openai-completions", provider="test", model="test", stop_reason="toolUse", timestamp=0)
    result = asyncio.run(_execute_tool_calls([tool], message, None, EventStream(), None, {}, safe_point_hook=checkpoint))
    assert order[:2] == ["review", "tool.before"]
    assert order.count("review") == 1
    assert ("execute" in order) is not blocked
    assert result["tool_results"][0].is_error is blocked


@pytest.mark.parametrize("use_safe_point", [False, True])
def test_rule_revoked_during_review_never_executes(monkeypatch, use_safe_point):
    rules = None
    called = []
    monkeypatch.setattr("openprogram.programs.permission_rule.load_merged_rules", lambda _: rules)

    async def classify(*args, **kwargs):
        nonlocal rules
        rules = PermissionRules(deny=["bash"])
        return False, "safe"

    async def execute(*args):
        called.append(True)
        return AgentToolResult(content=[], details={})

    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    req = TurnRequest(session_id="revocation", user_text="inspect", agent_id="main", source="web",
        permission_mode="auto", **owner_authority("owner/install/0123456789abcdef"))
    tool = wrap_with_approval(AgentTool(name="bash", label="bash", description="shell", parameters={}, execute=execute), req, lambda _: None)

    async def run():
        if use_safe_point:
            await tool._permission_preflight("c", {"command": "git status"})
            tool._interaction_manifest("c", {"command": "git status"})
        return await tool.execute("c", {"command": "git status"}, None, None)

    result = asyncio.run(run())
    assert result.is_error
    assert result.details["reason_code"] == "PERMISSION_REVIEW_STALE"
    assert not called


@pytest.mark.parametrize("with_manifest", [False, True])
def test_argument_mutation_during_review_never_executes(monkeypatch, with_manifest):
    args = {"command": "git status"}
    called = []
    monkeypatch.setattr("openprogram.programs.permission_rule.load_merged_rules", lambda _: None)

    async def classify(name, reviewed, **kwargs):
        assert reviewed == {"command": "git status"}
        args["command"] = "rm -rf important-files"
        return False, "safe"

    async def execute(*values):
        called.append(values)
        return AgentToolResult(content=[], details={})

    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    req = TurnRequest(session_id="mutation", user_text="inspect", agent_id="main", source="web",
        permission_mode="auto", **owner_authority("owner/install/0123456789abcdef"))
    tool = wrap_with_approval(AgentTool(name="bash", label="bash", description="shell", parameters={}, execute=execute), req, lambda _: None)
    async def run():
        if with_manifest:
            await tool._permission_preflight("c", args)
            tool._interaction_manifest("c", args)
        return await tool.execute("c", args, None, None)
    result = asyncio.run(run())
    assert result.is_error
    assert result.details["reason_code"] == "PERMISSION_REVIEW_STALE"
    assert not called


def test_preflight_uses_validated_operation(monkeypatch):
    seen = []
    monkeypatch.setattr("openprogram.programs.permission_rule.load_merged_rules", lambda _: None)

    async def classify(name, args, **kwargs):
        seen.append(("review", args))
        return False, "safe"

    async def execute(call_id, args, *_):
        seen.append(("execute", args))
        return AgentToolResult(content=[], details={})

    async def checkpoint(kind, payload):
        if kind == "tool.before":
            seen.append(("dispatch", payload["arguments"]))
        return False

    monkeypatch.setattr("openprogram.agent.permissions.classifier.auto_classify_tool", classify)
    request = TurnRequest(session_id="canonical", user_text="test", agent_id="main", source="web",
        permission_mode="auto", **owner_authority("owner/install/0123456789abcdef"))
    tool = wrap_with_approval(AgentTool(name="probe", label="probe", description="test",
        parameters={"type": "object", "properties": {"count": {"type": "integer"}}}, execute=execute), request, lambda _: None)
    message = AssistantMessage(content=[ToolCall(id="c", name="probe", arguments={"count": "1"})],
        api="openai-completions", provider="test", model="test", stop_reason="toolUse", timestamp=0)
    result = asyncio.run(_execute_tool_calls([tool], message, None, EventStream(), None, {}, safe_point_hook=checkpoint))
    assert not result["tool_results"][0].is_error
    assert seen == [("review", {"count": 1}), ("dispatch", {"count": 1}), ("execute", {"count": 1})]


def test_host_outcome_is_projected_to_live_event():
    from openprogram.agent.types import AgentEventToolEnd
    from openprogram.agent.internals._event_parsing import agent_event_to_envelope
    from types import SimpleNamespace
    event = AgentEventToolEnd(tool_call_id="c", tool_name="bash", is_error=True,
                             outcome="not_started", result=AgentToolResult(content=[], details={}))
    frame = agent_event_to_envelope(event, SimpleNamespace(session_id="s", user_msg_id="u"))
    assert frame["data"]["event"]["outcome"] == "not_started"
