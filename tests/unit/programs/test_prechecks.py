"""A call that would fail on its arguments is refused before it asks for approval."""
from __future__ import annotations

import asyncio

from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.models.providers.types import TextContent


def _prepare(plan):
    return {"action": "prepare", "worktree_id": "wt_1", "candidate_sha": "a" * 40, "goal": "docs",
            "assertions": ["the page names help"], "verification_plan": plan}


def _check(entry="/docs/install/upgrade.html", budget=65536):
    return {"schema": 1, "checks": [{"id": "page", "assertion_id": "acceptance-1", "entry": entry,
                                     "timeout_seconds": 30, "max_output_bytes": budget}]}


def test_self_update_prepare_checks_its_plan_before_approval():
    from openprogram.programs._authoring import prechecks
    import openprogram.programs.tools.system.self_update  # noqa: F401 — registers its check
    assert prechecks.problem("self_update", _prepare(_check())) is None
    assert "verification_plan" in prechecks.problem("self_update", _prepare(_check(budget=4_000_000)))
    assert "candidate_sha" in prechecks.problem("self_update", {**_prepare(None), "candidate_sha": "HEAD"})
    assert prechecks.problem("self_update", {"action": "status"}) is None


def test_an_invalid_call_never_reaches_the_approval_card(monkeypatch):
    from openprogram.runtime.permissions.authority import owner_authority
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.approval import wrap_with_approval
    import openprogram.programs.tools.system.self_update  # noqa: F401
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: None)
    events, ran = [], []

    async def execute(call_id, args, cancel, on_update):
        ran.append(args)
        return AgentToolResult(content=[TextContent(text="prepared")])
    req = TurnRequest(session_id="prechecks", user_text="t", agent_id="main", source="web",
                      permission_mode="bypass", **owner_authority("owner/install/0123456789abcdef"))
    tool = wrap_with_approval(AgentTool(name="self_update", label="self_update", description="update",
                                        parameters={"type": "object"}, execute=execute), req, events.append)
    refused = asyncio.run(tool.execute("c1", _prepare(_check(budget=4_000_000)), None, None))
    assert refused.is_error and refused.details["reason_code"] == "INVALID_ARGUMENTS"
    assert not ran and not events
