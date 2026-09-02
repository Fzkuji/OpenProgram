"""Execution-scoped authorization with non-disclosing denials.

The transport authenticates an actor; this module resolves the exact
execution, project and session boundary before an execution read or action is
allowed.  It deliberately returns one ``not_found`` result for unknown and
unauthorized targets so a caller cannot enumerate executions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from openprogram.agent.authority import has_capability, normalize_authority


POLICY_VERSION = "execution-policy-v1"

_READ_ACTIONS = frozenset({"execution.snapshot", "execution.events", "audit.read"})
_CONTROL_ACTIONS = frozenset({
    "execution.pause", "execution.continue", "execution.step", "execution.steer",
    "execution.cancel", "execution.fork", "execution.retry", "execution.wait.answer",
    "execution.wait.decline", "execution.reconcile",
})
_WAIT_ACTIONS = frozenset({"execution.wait.answer", "execution.wait.decline"})
_REVISION_ACTIONS = frozenset({
    "revision.draft.create", "revision.draft.get", "revision.draft.replace",
    "revision.draft.discard", "revision.validate", "revision.approve",
    "revision.publish",
})
_KNOWN_ACTIONS = _READ_ACTIONS | _CONTROL_ACTIONS | _REVISION_ACTIONS


class ExecutionAuthorizationError(RuntimeError):
    code = "not_found"


@dataclass(frozen=True)
class ExecutionAuthorization:
    allowed: bool
    action: str
    policy_version: str
    project_binding: Mapping[str, str]
    actor_binding: Mapping[str, str]


def _scope_contains(actor: Mapping[str, Any], field: str, expected: str) -> bool:
    value = actor.get(field)
    if value is None:
        return True
    if not isinstance(value, (list, tuple, frozenset, set)):
        return False
    return expected in value


def authorize_execution_action(
    actor: Mapping[str, Any] | Any,
    action: str,
    execution: Any,
    project_binding: Mapping[str, Any],
) -> ExecutionAuthorization:
    """Authorize one exact execution action or raise non-disclosing denial."""
    raw = actor if isinstance(actor, Mapping) else {}
    normalized = normalize_authority(raw)
    project_id = project_binding.get("project_id") if isinstance(project_binding, Mapping) else None
    session_id = project_binding.get("session_id") if isinstance(project_binding, Mapping) else None
    if (
        action not in _KNOWN_ACTIONS
        or not normalized
        or not isinstance(project_id, str)
        or not project_id
        or session_id != getattr(execution, "session_id", None)
        or not _scope_contains(raw, "project_ids", project_id)
        or not _scope_contains(raw, "session_ids", str(session_id))
    ):
        raise ExecutionAuthorizationError("execution is not visible")
    grants = raw.get("execution_actions")
    if grants is not None and (
        not isinstance(grants, (list, tuple, frozenset, set)) or action not in grants
    ):
        raise ExecutionAuthorizationError("execution is not visible")
    capability = (
        "runtime.wait" if action in _WAIT_ACTIONS
        else "runtime.control" if action in _CONTROL_ACTIONS
        else "fs.read"
    )
    if action in _REVISION_ACTIONS:
        capability = "runtime.control"
    # Local owner authority is the policy root.  Paired and MCP actors do not
    # gain execution reads merely because a transport supplied an execution id.
    if normalized.get("authority_tier") != "owner" or not has_capability(normalized, capability):
        raise ExecutionAuthorizationError("execution is not visible")
    return ExecutionAuthorization(
        allowed=True,
        action=action,
        policy_version=POLICY_VERSION,
        project_binding={"project_id": project_id, "session_id": str(session_id)},
        actor_binding=normalized,
    )
