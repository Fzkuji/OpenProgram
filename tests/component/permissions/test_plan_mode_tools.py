"""Changing the permission mode or a deny rule leaves the tools array alone
(deferred-tools A7).

Entering or leaving plan mode sends the same array; a call plan mode
forbids is refused where it is dispatched (Codex). A whole-tool deny rule
leaves the array as it is for the rest of the epoch and refuses the calls;
the next epoch (after a summary, a model switch) leaves the tool out.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.models.providers.types import TextContent
from openprogram.runtime.agent.loop import tool_epoch
from tests.support.request_bytes import call, install_bench, lookup_tool, reply, text_tool


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    class Nothing:
        def search(self, query, **_kwargs):
            return ""

    monkeypatch.setattr("openprogram.state.memory.get_backend", lambda: Nothing())
    tool_epoch.forget()
    yield
    tool_epoch.forget()


def _array(context) -> str:
    return json.dumps([[t.name, t.description, t.parameters] for t in context.tools], sort_keys=True)


def test_entering_and_leaving_plan_mode_sends_the_same_array(monkeypatch, tmp_path):
    tools = [lookup_tool(), text_tool("write", lambda arguments: "written")]
    bench = install_bench(monkeypatch, tmp_path, "anthropic", tools)
    model = bench.model
    bench.turn("look up a", reply(model, TextContent(text="42")))
    bench.turn("plan it", call(model, 1, name="write", key="x"), reply(model, TextContent(text="planned")),
               permission_mode="plan")
    bench.turn("go", reply(model, TextContent(text="done")))

    arrays = {_array(context) for context in bench.contexts}
    assert len(arrays) == 1, arrays
    assert "write" in next(iter(arrays))
    # The write in plan mode was refused at dispatch, not hidden.
    refused = [m for m in bench.contexts[2].messages if getattr(m, "role", None) == "toolResult"]
    assert refused and refused[-1].is_error
    assert "Plan mode" in refused[-1].content[0].text


def _tool(name: str, visible: dict):
    tool = SimpleNamespace(name=name)
    tool._permission_epoch_visible = lambda: visible.get(name, True)
    return tool


def test_a_deny_rule_takes_a_tool_out_only_from_the_next_epoch():
    visible = {}
    tools = [_tool("bash", visible), _tool("read", visible)]
    first = tool_epoch.epoch_for("s-deny", "epoch-1", tools)
    assert first.withheld == frozenset()

    visible["bash"] = False  # the owner adds a deny rule for bash mid-epoch
    same = tool_epoch.epoch_for("s-deny", "epoch-1", tools)
    assert same.withheld == frozenset(), "the array does not shrink inside the epoch"

    after_summary = tool_epoch.epoch_for("s-deny", "epoch-2", tools)
    assert after_summary.withheld == frozenset({"bash"})


def test_the_epoch_key_changes_with_a_summary_or_a_model_switch():
    from openprogram.models.providers.types import UserMessage

    model = SimpleNamespace(provider="anthropic", id="claude-fable-5-2")
    start = [UserMessage(content="hello", timestamp=0)]
    later = [*start, UserMessage(content="more", timestamp=1)]
    summary = [UserMessage(content="Summary of the conversation so far.", timestamp=2)]
    assert tool_epoch.epoch_key(model, start) == tool_epoch.epoch_key(model, later)
    assert tool_epoch.epoch_key(model, summary) != tool_epoch.epoch_key(model, later)
    other = SimpleNamespace(provider="openai", id="gpt-5.5")
    assert tool_epoch.epoch_key(other, later) != tool_epoch.epoch_key(model, later)


def test_the_submit_tool_stays_once_an_epoch_sent_it():
    submit = {"name": "submit_result", "description": "Submit.", "parameters": {"type": "object"}}
    record = tool_epoch.epoch_for("s-submit", "epoch-1", [], submit)
    assert record.submit == submit
    assert tool_epoch.epoch_for("s-submit", "epoch-1", [], None).submit == submit
    later = tool_epoch.epoch_for("s-submit-2", "epoch-1", [], None)
    assert later.submit is None
    assert tool_epoch.epoch_for("s-submit-2", "epoch-1", [], submit).submit == submit


def test_exit_plan_mode_outside_plan_mode_is_refused_at_dispatch(monkeypatch):
    from openprogram.models.providers.types import AgentTool, AgentToolResult
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.authority import owner_authority
    from openprogram.runtime.permissions.policy import permission_decision

    owner = "owner/install/0123456789abcdef"
    monkeypatch.setattr("openprogram.runtime.permissions.authority.owner_principal_id", lambda: owner)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: None)

    async def execute(*args):
        return AgentToolResult(content=[], details={})

    tool = AgentTool(name="exit_plan_mode", label="exit_plan_mode", description="probe",
                     parameters={"type": "object"}, execute=execute)

    def request(mode):
        return TurnRequest(session_id="plan-tools", user_text="x", agent_id="main", source="web",
                           permission_mode=mode, permission_rules=None, **owner_authority(owner))

    refused = permission_decision(tool, request("ask"), {"plan": "p"})
    assert (refused.outcome, refused.code) == ("deny", "PLAN_MODE_NOT_ACTIVE")
    assert permission_decision(tool, request("plan"), {"plan": "p"}).code == "PLAN_EXIT"
