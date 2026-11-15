"""An Agent holds what it sends for the user as a draft the user approves."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.extensions.mcp.adapter import sends_on_behalf


def _decided(decision):
    return decision.outcome, decision.code


def _tool(name: str, read_only: bool | None = None):
    return SimpleNamespace(name=name, annotations=SimpleNamespace(readOnlyHint=read_only))


@pytest.mark.parametrize("name", [
    "send_email", "chat.postMessage", "reply_to_thread", "create_comment",
    "gmail_send_message", "slack_post_message", "addComment", "forward_email",
])
def test_tools_that_send_are_marked(name):
    assert sends_on_behalf(_tool(name))


@pytest.mark.parametrize("name", [
    "list_messages", "get_posts", "search_email", "create_draft", "create_issue",
    "read_thread", "get_share_link", "update_page",
])
def test_reads_and_drafts_are_not(name):
    assert not sends_on_behalf(_tool(name))


def test_a_read_only_hint_wins():
    assert not sends_on_behalf(_tool("send_digest", read_only=True))


@pytest.fixture
def decide(monkeypatch):
    from openprogram.runtime.permissions import tool_authority
    from openprogram.runtime.permissions import identity, policy
    from openprogram.runtime.agent_registry import manager
    monkeypatch.setattr(identity, "scope_violation", lambda *a, **k: None)
    monkeypatch.setattr(policy, "can_approve", lambda req: req.source == "web")
    monkeypatch.setattr(tool_authority, "decide_tool_authority",
                        lambda *a, **k: SimpleNamespace(allowed=True, reason_code="", capability=""))
    tools = {"mail__send_email": SimpleNamespace(name="mail__send_email", _sends_on_behalf=True),
             "mail__list_messages": SimpleNamespace(name="mail__list_messages", _sends_on_behalf=False)}
    monkeypatch.setattr("openprogram.programs._host.runtime.get", tools.get)
    held = {"scout": True}
    monkeypatch.setattr(manager, "get", lambda agent_id: SimpleNamespace(hold_outbound=held[agent_id]))

    def run(name, *, mode="bypass", allow=(), ask=(), source="web", snapshot=None):
        rules = SimpleNamespace(allow=list(allow), ask=list(ask), deny=[])
        req = SimpleNamespace(permission_mode=mode, permission_rules=rules, session_id="s1",
                              source=source, surface_context=None, agent_id="scout",
                              profile_snapshot=snapshot)
        run.decision = policy.permission_decision(tools[name], req, {"to": "dana@example.com"})
        return _decided(run.decision)
    run.held = held
    return run


def test_a_send_asks_in_every_mode_even_with_an_allow_rule(decide):
    for mode in ("bypass", "auto", "ask", "acceptEdits"):
        assert decide("mail__send_email", mode=mode, allow=["mail__send_email"]) == ("ask", "RULE_OUTBOUND_HOLD")
    assert decide("mail__list_messages")[0] == "allow"


def test_nobody_can_approve_a_send_from_a_scheduled_run(decide):
    assert decide("mail__send_email", source="scheduler")[0] == "deny"


def test_a_refused_send_says_to_hand_over_the_draft_and_names_the_checkbox(decide):
    assert decide("mail__send_email", source="scheduler") == ("deny", "NO_ONE_TO_APPROVE")
    text = decide.decision.refusal.text
    assert "full draft in your final message" in text
    assert "Hold what it sends for my approval" in text and "Always-on tab" in text
    assert "Bypass permissions" not in text and "allow rule" not in text


def test_a_call_stopped_by_an_ask_rule_names_that_rule_and_where_it_lives(decide, monkeypatch):
    from openprogram.runtime.permissions import rule
    monkeypatch.setattr(rule, "rule_origin", lambda session_id, raw, behavior: "this project")
    decide.held["scout"] = False
    decide("mail__send_email", source="scheduler", ask=["mail__send_email"])
    text = decide.decision.refusal.text
    assert decide.decision.code == "NO_ONE_TO_APPROVE"
    assert 'edits or removes the ask rule "mail__send_email" in Permission rules in this project' in text
    assert "Bypass permissions" not in text


def test_an_external_client_is_not_told_to_add_a_rule_that_cannot_lift_the_block():
    from openprogram.runtime.permissions.refusal import MCP_GATE_REFUSALS
    assert "allow rule" not in MCP_GATE_REFUSALS["NO_ONE_TO_APPROVE"].text


def test_a_trial_of_an_unsaved_draft_with_the_box_ticked_holds(decide):
    decide.held["scout"] = False
    assert decide("mail__send_email", snapshot={"hold_outbound": True}) == ("ask", "RULE_OUTBOUND_HOLD")


def test_a_trial_cannot_lift_a_hold_the_saved_agent_has(decide):
    assert decide("mail__send_email", snapshot={"hold_outbound": False}) == ("ask", "RULE_OUTBOUND_HOLD")


def test_a_snapshot_without_the_hold_changes_nothing(decide):
    decide.held["scout"] = False
    assert decide("mail__send_email", snapshot={"hold_outbound": False})[0] == "allow"
    assert decide("mail__send_email", snapshot={})[0] == "allow"


def test_turning_the_hold_off_restores_the_mode(decide):
    decide.held["scout"] = False
    assert decide("mail__send_email")[0] == "allow"


def test_each_draft_is_approved_alone(monkeypatch):
    from openprogram.runtime.permissions.policy import one_shot_approval
    monkeypatch.setattr("openprogram.programs._host.runtime.get",
                        lambda name: SimpleNamespace(_sends_on_behalf=name == "mail__send_email"))
    assert one_shot_approval("mail__send_email", {})
    assert not one_shot_approval("mail__list_messages", {})
