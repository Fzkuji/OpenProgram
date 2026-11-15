"""Shared approval boundary contracts."""
import pytest
from openprogram.runtime.permissions.authority import owner_authority
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.permissions.approval import wrap_with_approval
from openprogram.runtime.permissions.modes import PermissionRules
from openprogram.models.providers.types import AgentTool, AgentToolResult

@pytest.mark.parametrize("name,rules,args", [
    ("bash", PermissionRules(ask=["bash"]), {}),
])
def test_bypass_preserves_required_approval_manifest(name, rules, args, monkeypatch):
    monkeypatch.setattr("openprogram.runtime.permissions.authority.owner_principal_id",
                        lambda: "owner/install/0123456789abcdef")
    # The interactive wrapper reads current project rules at each operation.
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: rules)
    async def execute(*args):
        return AgentToolResult(content=[], details={})
    req = TurnRequest(session_id="manifest-audit", user_text="test", agent_id="main",
        source="web", permission_mode="bypass", permission_rules=rules,
        **owner_authority("owner/install/0123456789abcdef"))
    tool = AgentTool(name=name, label=name, description="probe", parameters={"type":"object"}, execute=execute)
    wrapped = wrap_with_approval(tool, req, lambda event: None)
    manifest = wrapped._interaction_manifest("call-1", args)
    assert manifest is not None
    assert manifest["kind"] == "approval"
    # Declining is the user's answer: the model hears it, the run goes on.
    assert manifest["policy_snapshot"]["on_decline"] == "continue"


@pytest.mark.parametrize("name,args", [
    ("self_update", {"action": "prepare", "worktree_id": "wt_1", "candidate_sha": "a" * 40,
                     "goal": "docs", "assertions": ["the page names help"]}),
    ("exit_plan_mode", {}),
])
def test_unrestricted_shows_no_card_for_a_tool_that_used_to_force_one(name, args, monkeypatch):
    monkeypatch.setattr("openprogram.runtime.permissions.authority.owner_principal_id",
                        lambda: "owner/install/0123456789abcdef")
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: None)
    async def execute(*args):
        return AgentToolResult(content=[], details={})
    req = TurnRequest(session_id="no-card", user_text="test", agent_id="main",
        source="web", permission_mode="bypass", permission_rules=None,
        **owner_authority("owner/install/0123456789abcdef"))
    tool = AgentTool(name=name, label=name, description="probe", parameters={"type":"object"}, execute=execute)
    assert wrap_with_approval(tool, req, lambda event: None)._interaction_manifest("call-1", args) is None


def test_a_turn_probes_for_desktop_grants_before_its_first_application_step_only(monkeypatch):
    """Each probe starts a process; six clicks in a row used to start six."""
    import sys
    from openprogram.host import system_access

    monkeypatch.setattr("openprogram.runtime.permissions.authority.owner_principal_id",
                        lambda: "owner/install/0123456789abcdef")
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: None)
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    probes, accessibility = [], ["not_granted"]

    def probe():
        probes.append(accessibility[0])
        return {"identity": {"executable": sys.executable}, "capabilities": {
            "screen_recording": {"status": "granted", "detail": "fresh"},
            "accessibility": {"status": accessibility[0], "detail": "fresh"}}}

    monkeypatch.setattr(system_access, "_native_probe", probe)

    async def execute(*args):
        return AgentToolResult(content=[], details={})

    def turn():
        req = TurnRequest(session_id="desktop-grants", user_text="test", agent_id="main",
                          source="web", permission_mode="bypass", permission_rules=None,
                          **owner_authority("owner/install/0123456789abcdef"))
        tool = AgentTool(name="computer_use_agent", label="computer_use_agent", description="probe",
                         parameters={"type": "object"}, execute=execute)
        return wrap_with_approval(tool, req, lambda event: None)

    click = {"target": "app", "action": "click", "args": {"app": "Notes", "element_index": 3}}
    wrapped = turn()
    # Not granted yet: the step waits for the grant, and each call looks again.
    assert wrapped._interaction_manifest("call-1", click)["kind"] == "system_access"
    assert wrapped._interaction_manifest("call-2", click)["kind"] == "system_access"
    assert len(probes) == 2

    accessibility[0] = "granted"
    for number in range(3, 9):
        assert wrapped._interaction_manifest(f"call-{number}", click) is None
    assert len(probes) == 3

    # The next turn looks once more, then not again.
    wrapped = turn()
    assert wrapped._interaction_manifest("call-1", click) is None
    assert wrapped._interaction_manifest("call-2", click) is None
    assert len(probes) == 4
