"""A policy edit while an operation waits in review: stale only when the operation's own decision moves."""
import asyncio

import pytest

from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.permissions import update_permission
from openprogram.runtime.permissions.authority import local_owner_authority
from openprogram.runtime.permissions.lifecycle import wrap_live_permission
from openprogram.runtime.permissions.modes import PermissionRules
from openprogram.state.store.session.session_store import SessionStore


@pytest.fixture
def live(tmp_path, monkeypatch):
    db = SessionStore(tmp_path / "sessions")
    db.create_session("review", "main", permission_mode="ask")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    rules = PermissionRules(allow=["open_work"])
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _: rules)
    actor = local_owner_authority()
    calls = []

    async def execute(*args):
        calls.append(args[0])
        return AgentToolResult(content=[], details={})

    request = TurnRequest(session_id="review", user_text="note it", agent_id="main", source="web",
                          permission_mode="ask", **actor)
    tool = wrap_live_permission(AgentTool(name="open_work", label="open_work", description="notes",
                                          parameters={"type": "object"}, execute=execute), request, lambda _: None)
    return tool, rules, actor, calls


def _run(tool, call_id, args, between):
    async def go():
        await tool._permission_preflight(call_id, args)
        between()
        return await tool.execute(call_id, args, None, None)
    return asyncio.run(go())


def test_rule_for_another_tool_leaves_the_review_standing(live):
    tool, rules, _, calls = live
    result = _run(tool, "c1", {"action": "add"}, lambda: rules.allow.append("computer_use_agent(app)"))
    assert not result.is_error
    assert calls == ["c1"]


def test_switch_to_bypass_during_review_runs_the_operation(live):
    tool, rules, actor, calls = live
    rules.allow.clear()
    result = _run(tool, "c2", {"action": "add"}, lambda: update_permission("review", "bypass", 0, actor))
    assert not result.is_error
    assert calls == ["c2"]


def test_deny_added_during_review_is_still_stale(live):
    tool, rules, _, calls = live
    result = _run(tool, "c3", {"action": "add"}, lambda: rules.deny.append("open_work"))
    assert result.details["reason_code"] == "PERMISSION_REVIEW_STALE"
    assert calls == []
