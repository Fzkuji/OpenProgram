"""Shared approval boundary contracts."""
import pytest
from openprogram.agent.authority import owner_authority
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.internals._approval import wrap_with_approval
from openprogram.agent.session_config import PermissionRules
from openprogram.agent.types import AgentTool, AgentToolResult

@pytest.mark.parametrize("name,rules", [
    ("bash", PermissionRules(ask=["bash"])),
    ("self_update_prepare", None),
    ("exit_plan_mode", None),
])
def test_bypass_preserves_required_approval_manifest(name, rules):
    async def execute(*args):
        return AgentToolResult(content=[], details={})
    req = TurnRequest(session_id="manifest-audit", user_text="test", agent_id="main",
        source="web", permission_mode="bypass", permission_rules=rules,
        **owner_authority("owner/install/0123456789abcdef"))
    tool = AgentTool(name=name, label=name, description="probe", parameters={"type":"object"}, execute=execute)
    wrapped = wrap_with_approval(tool, req, lambda event: None)
    manifest = wrapped._interaction_manifest("call-1", {})
    assert manifest is not None
    assert manifest["kind"] == "approval"
