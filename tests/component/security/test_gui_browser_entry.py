"""Public entry through actual Runtime/AgentSession with a controlled provider."""
import json
import re
import sys

import pytest

from tests.component.security.test_gui_agent import owned
from tests.component.security.test_gui_browser_resources import pages

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS sandbox")


def runtime_for_browser(*, stale=False, value="after", denied=False):
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.providers.types import Model, AssistantMessage, ToolCall, TextContent, EventDone
    runtime = Runtime(call=lambda *args, **kwargs: "unused", model="fixture", max_retries=1)
    runtime._call_fn = None
    runtime.api_model = Model(id="fixture", name="fixture", api="openai-completions", provider="openai", base_url="https://example.invalid/v1")
    seen = []
    async def provider(model, context, options):
        seen.append(context)
        if len(seen) == 1:
            text = "\n".join(getattr(block, "text", "") for message in context.messages for block in (message.content if isinstance(message.content, list) else []))
            catalog = re.search(r"GUI_CATALOG_HANDLE=([0-9a-f]+)", text).group(1)
            code = f"import json\nc = {catalog!r}\nt = (await ui.call(c, 'list'))['value']['pages'][0]['page_context_token']\np = (await ui.call(c, 'acquire', {{'page_context_token': t}}))['value']['json_data']\nawait ui.call(p['handle'], 'type', {{'ref': 'field', 'text': 'after'}}, observation=p['frame_id'])\nf = (await ui.call(p['handle'], 'observe'))['value']['frame_id']\nprint(json.dumps({{'handle': p['handle'], 'frame_id': f}}))"
            content, reason = [ToolCall(id="gui-call", name="gui_exec", arguments={"code": code})], "toolUse"
        else:
            message = [m for m in context.messages if m.role == "toolResult"][-1]
            if denied:
                assert message.is_error
                result = {"status": "failed", "summary": "denied", "handle": "", "frame_id": "", "assertion": "", "value": ""}
            else:
                assert not message.is_error, message
                result_text = next(c.text for c in message.content if c.type == "text")
                resource = json.loads(json.loads(result_text)["stdout"])
                result = {"status": "succeeded", "summary": "entered after", **resource, "assertion": "text_contains", "value": value}
                if stale: result["frame_id"] = "f1"
            content, reason = [TextContent(text=json.dumps(result))], "stop"
        reply = AssistantMessage(content=content, api=model.api, provider=model.provider, model=model.id, stop_reason=reason, timestamp=1)
        yield EventDone(reason=reason, message=reply)
    runtime._stream_fn = provider
    return runtime, seen


def install(owned, pages, monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.gui_harness_bridge import install_gui_harness_web_use
    registry, context, state, released = pages
    monkeypatch.setattr(surface_context, "capture_pages", lambda current: context)
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    def old(**kwargs):
        raise AssertionError("legacy browser planner called")
    return install_gui_harness_web_use(old)


def test_public_browser_entry_uses_standard_runtime_and_host_final_verification(owned, pages, monkeypatch):
    wrapped = install(owned, pages, monkeypatch)
    runtime, seen = runtime_for_browser()
    try:
        result = wrapped(task="Enter after and verify it", surface="browser", max_steps=3, max_seconds=10, runtime=runtime)
    finally:
        runtime.close()
    assert result["status"] == "succeeded", json.dumps(result)
    assert result["verification"]["evidence"]["passed"] is True
    assert owned.effects.get(result["verification"]["effect_id"]) is not None
    assert len(seen) == 2 and pages[2]["text"] == "after"
    assert not pages[0]._sessions and not pages[0]._page_leases and not pages[0]._page_capabilities


@pytest.mark.parametrize("stale,value", [(True, "after"), (False, "missing")])
def test_public_entry_rejects_stale_or_failed_final_assertion(owned, pages, monkeypatch, stale, value):
    wrapped = install(owned, pages, monkeypatch)
    runtime, seen = runtime_for_browser(stale=stale, value=value)
    try:
        result = wrapped(task="Enter after", surface="browser", max_steps=3, max_seconds=10, runtime=runtime)
    finally:
        runtime.close()
    assert result["status"] == "failed" and result["success"] is False
    assert not pages[0]._sessions and not pages[0]._page_leases


@pytest.mark.parametrize("complete_authority", [False, True])
def test_standard_runtime_inherits_outer_gui_tool_denial(owned, pages, monkeypatch, complete_authority):
    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.session_config import PermissionRules
    from openprogram.agent.turn_request_context import set_turn_request, reset_turn_request
    wrapped = install(owned, pages, monkeypatch)
    runtime, seen = runtime_for_browser(denied=True)
    from openprogram.agent.authority import owner_authority
    authority = owner_authority("owner/install/0123456789abcdef") if complete_authority else {}
    token = set_turn_request(TurnRequest(session_id="session", user_text="work", agent_id="main", source="web", permission_mode="bypass", permission_rules=PermissionRules(deny=["gui_exec"]), **authority))
    try:
        result = wrapped(task="Enter after", surface="browser", max_steps=3, max_seconds=10, runtime=runtime)
    finally:
        reset_turn_request(token)
        runtime.close()
    assert len(seen) == 2 and result["status"] == "failed"
    assert pages[2]["text"] == "before" and not pages[0]._sessions
    assert owned.effects.list_unresolved("exec") == []


def test_unsupported_explicit_backend_does_not_capture_or_call_old_loop(owned, pages, monkeypatch):
    wrapped = install(owned, pages, monkeypatch)
    from openprogram.agent import surface_context
    monkeypatch.setattr(surface_context, "capture_pages", lambda context: pytest.fail("must not capture"))
    result = wrapped(task="Inspect", backend="chrome_devtools_mcp", runtime=object())
    assert result["status"] == "infeasible" and result["reason_code"] == "guarded_dispatch_unsupported"


def test_plain_completion_with_invented_handle_is_not_success(owned, pages, monkeypatch):
    from openprogram.providers.types import AssistantMessage, TextContent, EventDone
    wrapped = install(owned, pages, monkeypatch)
    runtime, _ = runtime_for_browser()
    async def provider(model, context, options):
        proposal = {"status": "succeeded", "summary": "claimed done", "handle": "invented", "frame_id": "f1", "assertion": "text_contains", "value": "after"}
        message = AssistantMessage(content=[TextContent(text=json.dumps(proposal))], api=model.api, provider=model.provider, model=model.id, stop_reason="stop", timestamp=1)
        yield EventDone(reason="stop", message=message)
    runtime._stream_fn = provider
    try:
        result = wrapped(task="Enter after", surface="browser", runtime=runtime)
    finally:
        runtime.close()
    assert result["status"] == "failed" and pages[2]["text"] == "before"
    assert not pages[0]._sessions and not pages[0]._page_capabilities


def test_provider_cancellation_releases_owned_resources_and_propagates(owned, pages, monkeypatch):
    from openprogram.providers.utils.errors import ExecInterrupt
    from openprogram.agentic_programming.function import CancelledError
    wrapped = install(owned, pages, monkeypatch)
    runtime, _ = runtime_for_browser()
    async def provider(*args):
        raise ExecInterrupt("cancelled")
        yield
    runtime._stream_fn = provider
    try:
        with pytest.raises((ExecInterrupt, CancelledError)):
            wrapped(task="Enter after", surface="browser", runtime=runtime)
    finally:
        runtime.close()
    assert not pages[0]._sessions and not pages[0]._page_capabilities


def test_general_tools_do_not_replace_inherited_deny_policy(owned, pages, monkeypatch):
    from openprogram.agentic_programming.runtime import _current_tool_policy
    from openprogram import programs
    wrapped = install(owned, pages, monkeypatch)
    monkeypatch.setattr(programs, "agent_tools", lambda **kwargs: [])
    runtime, seen = runtime_for_browser(denied=True)
    token = _current_tool_policy.set({"deny": ["gui_exec"]})
    try:
        result = wrapped(task="Enter after", surface="browser", allow_general=True, runtime=runtime)
    finally:
        _current_tool_policy.reset(token)
        runtime.close()
    assert result["status"] == "failed" and pages[2]["text"] == "before"
    assert len(seen) == 2


def test_cleanup_failure_cannot_return_success(owned, pages, monkeypatch):
    wrapped = install(owned, pages, monkeypatch)
    adapter = pages[0]._adapters["open_claude_chrome"]
    original = adapter.close
    def failed_close(session):
        original(session)
        raise RuntimeError("owned cleanup failure")
    adapter.close = failed_close
    runtime, _ = runtime_for_browser()
    try:
        result = wrapped(task="Enter after", surface="browser", runtime=runtime)
    finally:
        runtime.close()
    assert result["status"] == "failed" and "owned cleanup failure" in result["error"]
    assert result["verification"]["evidence"]["passed"] is True
    assert not pages[0]._sessions and not pages[0]._page_leases


def test_strict_cleanup_attempts_every_session_before_raising(owned, pages):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    import asyncio
    registry, context, _, _ = pages
    context["surfaces"].append({"surface_key": "s2", "binding_id": "second-binding", "title": "Second"})
    adapter = registry._adapters["open_claude_chrome"]
    original, closed = adapter.close, []
    def failed_close(session):
        original(session)
        closed.append(session.id)
        if len(closed) == 1: raise RuntimeError("first cleanup failed")
    adapter.close = failed_close
    with GuiAgentTools() as gui:
        resources = GuiBrowserResources(gui.broker, registry, context)
        result = asyncio.run(gui.tool.execute("call", {"code": f"c = {resources.handle!r}\nfor p in (await ui.call(c, 'list'))['value']['pages']:\n    await ui.call(c, 'acquire', {{'page_context_token': p['page_context_token']}})"}, None, None))
        assert not result.is_error
        with pytest.raises(RuntimeError, match="first cleanup failed"):
            resources.close()
        assert len(closed) == 2
        assert not registry._sessions and not registry._page_leases and not registry._page_capabilities


@pytest.mark.parametrize("late", ["deadline", "cancel"])
def test_cleanup_cannot_return_late_success(owned, pages, monkeypatch, late):
    import threading
    import time
    from types import SimpleNamespace
    from openprogram.programs import gui_browser_agent
    from openprogram.agentic_programming.function import _current_cancel, CancelledError
    wrapped = install(owned, pages, monkeypatch)
    offset, cancelled = [0], threading.Event()
    monkeypatch.setattr(gui_browser_agent, "time", SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0]))
    adapter = pages[0]._adapters["open_claude_chrome"]
    original = adapter.close
    def late_close(session):
        original(session)
        if late == "deadline": offset[0] = 100
        else: cancelled.set()
    adapter.close = late_close
    token = _current_cancel.set(cancelled)
    runtime, _ = runtime_for_browser()
    try:
        if late == "cancel":
            with pytest.raises(CancelledError):
                wrapped(task="Enter after", surface="browser", max_seconds=10, runtime=runtime)
        else:
            result = wrapped(task="Enter after", surface="browser", max_seconds=10, runtime=runtime)
            assert result["status"] == "failed" and "deadline" in result["error"]
            assert result["verification"]["evidence"]["passed"] is True
    finally:
        _current_cancel.reset(token)
        runtime.close()
    assert not pages[0]._sessions and not pages[0]._page_leases
