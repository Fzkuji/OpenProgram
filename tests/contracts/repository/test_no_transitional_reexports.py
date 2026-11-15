"""A runtime module exports only the names it owns.

When ``openprogram.runtime`` was split by concept, names moved to the
package that owns them: execution binding to ``execution.run_control``, the
tool contract to ``models.providers.types``, ``TurnRequest`` to
``agent.turn_request``, the permission vocabulary to ``permissions.modes``,
call-scope ContextVars to ``context.scope`` and stored records to
``openprogram.state``. Their old modules no longer expose them, so callers
import each name from its owner and no compatibility re-export creeps back.
"""
from __future__ import annotations

import importlib

import pytest

MOVED: dict[str, tuple[str, ...]] = {
    "openprogram.runtime.conversation.run_control": (
        "get_current_session_id", "set_current_session_id", "reset_current_session_id",
        "get_current_execution_id", "set_current_execution_id", "reset_current_execution_id",
        "admit_child_execution", "child_execution_admission", "mark_execution_terminal",
        "mark_cancelled", "cancel_execution", "cancel_session_executions",
        "register_execution_owner", "retire_execution_owner", "owner_is_alive", "resume_cancel",
        "CancellationToken", "CancelToken", "current_token",
        "ExecutionNotFound", "ExecutionNotCancellable", "ExecutionSpawnRefused",
        "CancelledError", "_owners", "_current_tokens", "_current_token",
    ),
    "openprogram.runtime.agent.loop.types": (
        "AgentTool", "AgentToolResult", "AgentToolUpdateCallback",
    ),
    "openprogram.runtime.agent.loop": ("AgentTool", "AgentToolResult"),
    "openprogram.runtime.conversation.turn_runtime.types": (
        "TurnRequest", "PermissionMode", "INHERIT_PARENT", "_InheritParent",
    ),
    "openprogram.runtime.conversation.turn_runtime": (
        "TurnRequest", "PermissionMode", "INHERIT_PARENT",
        "_wrap_with_approval", "_await_user_approval",
    ),
    "openprogram.runtime.conversation.turn_request_context": (
        "current_turn_request", "get_turn_request", "set_turn_request",
        "reset_turn_request", "inner_turn_request",
    ),
    "openprogram.runtime.conversation.session_config": (
        "VALID_PERMISSION", "_PERMISSION_BY_LOWER",
    ),
    "openprogram.runtime.agent.call_state": ("default_expose", "_VALID_EXPOSE"),
    "openprogram.runtime.agent.workflow_site": ("current_entry", "current_site"),
    "openprogram.runtime.conversation.production_driver": (
        "AttemptRecord", "ExecutionStatus",
        "ExecutionStore", "RuntimeControlService", "validate_runtime_contract",
    ),
    "openprogram.runtime.permissions.authority": (
        "AuthorityDecision", "AuthorityTier", "TIER_CAPABILITIES", "MESSAGE_SCHEMA_FIELD",
        "MESSAGE_SCHEMA_VERSION", "decide_capability", "has_capability",
        "is_legacy_message", "stamp_schema",
    ),
    "openprogram.runtime.context.commit": (
        "ContextItem", "ContextCommit", "CURRENT_RULES_VERSION", "init_schema",
        "save_commit", "load_commit", "load_latest_commit", "load_commit_for_head",
        "list_commits",
    ),
    "openprogram.runtime.conversation.rewind": ("recover_all_rewinds",),
    "openprogram.runtime.jobs.resource_governance": (
        "RequestReservation", "plan_request_reservation",
    ),
    "openprogram.runtime.agent": ("Context", "LLMError", "StructuredOutputError"),
}


@pytest.mark.parametrize("module_name", sorted(MOVED))
def test_old_module_no_longer_exposes_moved_names(module_name):
    module = importlib.import_module(module_name)
    exposed = [name for name in MOVED[module_name] if hasattr(module, name)]
    assert exposed == [], f"{module_name} re-exports names it does not own: {exposed}"


@pytest.mark.parametrize("module_name", sorted(MOVED))
def test_all_lists_only_defined_names(module_name):
    module = importlib.import_module(module_name)
    missing = [name for name in getattr(module, "__all__", ()) if not hasattr(module, name)]
    assert missing == []
