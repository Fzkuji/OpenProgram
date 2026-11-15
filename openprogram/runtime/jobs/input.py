"""A background Agent Job's admission input, built from and turned into live objects.

The versioned envelope and its validation live in
``openprogram.runtime.execution.job_input``; this module adds the
conversions between it and a ``Job`` or a ``TurnRequest``.
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import fields
from typing import Any, Mapping

from openprogram.runtime.execution.job_input import (
    JOB_AGENT_INPUT_VERSION,
    _DEFERRED_KEYS,
    JobAgentInputError,
    JobAgentInputV1 as _JobAgentInputSchema,
)


def turn_request_fields() -> frozenset[str]:
    """Field names a stored Job turn request may carry."""
    from openprogram.runtime.agent.turn_request import TurnRequest
    return frozenset(field.name for field in fields(TurnRequest))


#: What a parent's permission snapshot may carry (``lifecycle.spawn_permission_snapshot``).
_SNAPSHOT_KEYS = frozenset({"mode", "rules", "additional_working_dirs", "sandbox_enabled"})


def _own_permissions(job: Any) -> dict[str, Any]:
    """The ``permissions`` block of the Agent a sub-agent runs as: from the
    frozen profile a trial carries, else the saved Agent."""
    from openprogram.runtime.agent_registry.manager import validate_permissions
    profile = getattr(job, "profile_snapshot", None)
    if isinstance(profile, Mapping) and "permissions" in profile:
        try:
            return validate_permissions(profile.get("permissions"))
        except ValueError as exc:
            # Unreadable narrowing must not run as "narrows nothing".
            raise JobAgentInputError(f"invalid Agent permissions: {exc}") from exc
    try:
        from openprogram.runtime.agent_registry import manager
        spec = manager.get(job.agent_id) if job.agent_id else None
    except Exception:  # noqa: BLE001 - an unreadable registry narrows nothing more
        spec = None
    return dict(getattr(spec, "permissions", None) or {})


class JobAgentInputV1(_JobAgentInputSchema):
    """Validated version-1 input and conversion to the existing turn DTO."""

    @classmethod
    def from_job(cls, job: Any, *, run_id: str | None = None,
                 parent_execution_id: str | None = None,
                 permission_snapshot: Mapping[str, Any] | None = None,
                 usage_goal: Mapping[str, Any] | None = None,
                 automation: str | None = None,
                 detached: bool = False,
                 asks_through: Mapping[str, Any] | None = None) -> "JobAgentInputV1":
        from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION
        from openprogram.state.authority import normalize_authority

        deferred_inbox = None
        if isinstance(job.deferred_inbox, Mapping):
            deferred_inbox = {
                key: job.deferred_inbox.get(key)
                for key in _DEFERRED_KEYS
            }
        caller_session = job.caller_session_id or job.parent_session_id
        caller = None
        if job.caller_msg_id:
            caller = {
                "execution_id": parent_execution_id or job.parent_job_id or job.id,
                "session_id": caller_session,
                "msg_id": job.caller_msg_id,
                "node_id": job.caller_msg_id,
            }
        authority = normalize_authority(job)
        verifier = job.source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}
        owner_work = not verifier and authority.get("authority_tier") == "owner"
        spawn_caller = job.spawn_caller if job.spawn_caller is not None else (
            caller["node_id"] if caller else None
        )
        if caller is None and spawn_caller is not None:
            caller = {
                "execution_id": parent_execution_id or job.parent_job_id or job.id,
                "session_id": caller_session,
                "msg_id": spawn_caller,
                "node_id": spawn_caller,
            }
        request: dict[str, Any] = {
            "session_id": job.parent_session_id,
            "user_text": job.prompt,
            "agent_id": job.agent_id,
            "source": job.source,
            "permission_mode": (
                "bypass" if verifier
                else DEFAULT_PERMISSION if owner_work
                else "ask"
            ),
            "branch_from": job.parent_msg_id,
            # A clean Job starts from an empty context. Persist that choice
            # in the canonical request instead of relying on a dispatcher
            # branch inference, so replay and alternate runners see the same
            # input contract.
            "history_override": [] if job.context_mode == "clean" else None,
            "tools_override": copy.deepcopy(job.tools_override),
            "model_override": job.model_override,
            "thinking_effort": job.thinking_effort,
            "render_range": copy.deepcopy(job.render_range),
            "profile_snapshot": copy.deepcopy(job.profile_snapshot),
            "response_format": copy.deepcopy(job.response_format),
            "advance_head": job.advance_head,
            "spawn_caller": spawn_caller,
            "spawned_from_session": caller["session_id"] if caller else None,
            **authority,
        }
        if job.message_origin:
            # Only a delivered message names an origin, so every other Job keeps
            # the payload and hash it always had.
            request["message_origin"] = job.message_origin
        if automation:
            request["automation"] = automation
        if detached:
            request["detached"] = True
        # Resolve the destination before permission defaults: the page may
        # be on a different branch from the delivered message.
        from openprogram.runtime.conversation.branch_config import freeze_branch_request
        request = freeze_branch_request(request)
        if permission_snapshot is not None:
            from openprogram.runtime.permissions.modes import VALID_PERMISSION
            if (job.source not in {"agent_spawn", "self_update_replan", "self_update_continue"}
                    or not {"mode", "rules"} <= set(permission_snapshot) <= _SNAPSHOT_KEYS
                    or permission_snapshot["mode"] not in VALID_PERMISSION):
                raise JobAgentInputError("invalid inherited permission snapshot")
            request["permission_mode"] = permission_snapshot["mode"]
            request["permission_rules"] = copy.deepcopy(permission_snapshot["rules"])
            if "additional_working_dirs" in permission_snapshot:
                request["additional_working_dirs"] = list(permission_snapshot["additional_working_dirs"])
            if "sandbox_enabled" in permission_snapshot:
                request["sandbox_enabled"] = permission_snapshot["sandbox_enabled"]
                request["sandbox_config_frozen"] = True
        elif owner_work and job.parent_session_id:
            # No live parent to inherit from (a message delivered once its
            # target went idle): the turn runs as one sent in that conversation.
            from openprogram.runtime.conversation.session_config import conversation_permission
            permission = conversation_permission(job.parent_session_id, **(
                {"branch_id": request["branch_id"]} if request.get("branch_id") else {}
            ))
            request["permission_mode"] = permission["permission_mode"]
            rules = permission["permission_rules"]
            request["permission_rules"] = dataclasses.asdict(rules) if dataclasses.is_dataclass(rules) else rules
        if (permission_snapshot is not None or (owner_work and job.parent_session_id)) \
                and isinstance(request.get("turn_settings"), dict):
            # The permission set here replaces the one the branch gave.
            record = request["turn_settings"]
            record["values"]["permission_mode"] = request["permission_mode"]
            record["sources"]["permission_mode"] = "parent" if permission_snapshot is not None else "branch"
        if job.source == "agent_spawn":
            # What the caller allows, cut by what the sub-agent's own Agent
            # allows (permissions/delegation.py); its own Agent never widens it.
            from openprogram.runtime.permissions.delegation import narrow_request
            narrow_request(request, _own_permissions(job))
            if asks_through:
                # A person waits on this sub-agent in the caller's conversation.
                request["asks_through"] = dict(asks_through)
        payload = {
            "version": JOB_AGENT_INPUT_VERSION,
            "kind": "job_agent",
            "turn_request": request,
            "job_context": {
                # This field reconstructs the Job resource parent. A normal
                # chat execution is recorded as caller.execution_id and on
                # the canonical execution, not as a resource-governed Job.
                "parent_execution_id": job.parent_job_id,
                "run_id": run_id,
                "branch_frontier": job.target_branch_head_id,
                "caller": caller,
                "worktree_id": job.worktree_id,
                "authority_snapshot": authority,
                "deferred_inbox": deferred_inbox,
                "chain": {
                    "messages": job.chain_messages,
                    "generations": job.chain_generations,
                },
                "relation": job.relation,
                "origin_turn_id": job.origin_turn_id,
                "resource_hints": {
                    "admission_id": job.admission_id,
                    "budget_scope_id": job.budget_scope_id,
                    "effective_limits": copy.deepcopy(job.effective_limits),
                    "resolved_limits_snapshot": copy.deepcopy(job.resolved_limits_snapshot),
                    "caller_generations": job.caller_chain_generations,
                },
            },
        }
        if usage_goal is not None:
            payload["job_context"]["usage_goal"] = dict(usage_goal)
        # Both are written only when set, so every other Job keeps the
        # payload and hash it always had.
        if job.chain_ancestors:
            payload["job_context"]["chain"]["ancestors"] = list(job.chain_ancestors)
        if job.tool_call_id:
            payload["job_context"]["tool_call_id"] = job.tool_call_id
        return cls.parse(payload)

    def to_turn_request(self, *, session_id: str | None = None) -> Any:
        from openprogram.runtime.agent.turn_request import TurnRequest

        values = copy.deepcopy(dict(self.turn_request))
        if session_id is not None and values.get("session_id") != session_id:
            raise JobAgentInputError("Job Agent input belongs to another session")
        if isinstance(values.get("permission_rules"), Mapping):
            from openprogram.runtime.permissions.modes import (
                as_permission_rules as _as_permission_rules,
            )
            values["permission_rules"] = _as_permission_rules(values["permission_rules"])
        return TurnRequest(**values)

    def to_job(self, *, execution_id: str, session_id: str) -> Any:
        """Rebuild the legacy Job projection from immutable Job input only."""
        from openprogram.runtime.jobs.types import Job

        request = dict(self.turn_request)
        context = dict(self.job_context)
        caller = context["caller"]
        hints = dict(context["resource_hints"])
        return Job(
            id=execution_id,
            parent_session_id=session_id,
            prompt=request["user_text"],
            agent_id=request["agent_id"],
            subject=request["user_text"][:60] or "job",
            description=request["user_text"],
            context_mode="clean" if request.get("branch_from") is None else "inherit",
            parent_msg_id=request.get("branch_from"),
            parent_job_id=context["parent_execution_id"],
            caller_msg_id=caller["msg_id"] if caller else None,
            caller_session_id=caller["session_id"] if caller else None,
            chain_messages=context["chain"]["messages"],
            chain_generations=context["chain"]["generations"],
            chain_ancestors=list(context["chain"].get("ancestors") or []) or None,
            tool_call_id=context.get("tool_call_id"),
            caller_chain_generations=hints["caller_generations"],
            spawn_caller=request.get("spawn_caller"),
            advance_head=bool(request.get("advance_head", False)),
            tools_override=copy.deepcopy(request.get("tools_override")),
            model_override=request.get("model_override"),
            thinking_effort=request.get("thinking_effort"),
            render_range=copy.deepcopy(request.get("render_range")),
            source=request.get("source") or "agent_spawn",
            profile_snapshot=copy.deepcopy(request.get("profile_snapshot")),
            response_format=copy.deepcopy(request.get("response_format")),
            message_origin=request.get("message_origin"),
            deferred_inbox=copy.deepcopy(context["deferred_inbox"]),
            target_branch_head_id=context["branch_frontier"],
            worktree_id=context["worktree_id"],
            creates_agent=context["relation"] == "owned",
            relation=context["relation"],
            origin_turn_id=context["origin_turn_id"],
            speaker_kind=request.get("speaker_kind"),
            speaker_id=request.get("speaker_id"),
            speaker_display=request.get("speaker_display"),
            principal_id=request.get("principal_id"),
            authority_tier=request.get("authority_tier"),
            interaction=request.get("interaction"),
            admission_id=hints["admission_id"],
            budget_scope_id=hints["budget_scope_id"],
            effective_limits=copy.deepcopy(hints["effective_limits"]),
            resolved_limits_snapshot=copy.deepcopy(hints["resolved_limits_snapshot"]),
        )
