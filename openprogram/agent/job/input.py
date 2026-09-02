"""Strict immutable input for a background Agent Job."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, fields
from typing import Any, Mapping


JOB_AGENT_INPUT_VERSION = 1
MAX_JOB_AGENT_INPUT_BYTES = 1024 * 1024
MAX_JOB_CHAIN_VALUE = 100_000
MAX_RESOURCE_HINT_VALUE = 10**15


class JobAgentInputError(ValueError):
    """A Job admission input violates its durable schema."""


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise JobAgentInputError("Job Agent input must be JSON serializable") from exc


def _object(value: Any, name: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - keys:
        raise JobAgentInputError(f"{name} has unknown fields")
    return copy.deepcopy(dict(value))


def _optional_string(value: Any, name: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise JobAgentInputError(f"{name} must be a non-empty string or null")
    return value


def _bounded_int(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise JobAgentInputError(f"{name} must be an integer between 0 and {maximum}")
    return value


def _bounded_json(value: Any, name: str, *, depth: int = 0) -> None:
    if depth > 16:
        raise JobAgentInputError(f"{name} is nested too deeply")
    if type(value) is int and abs(value) > MAX_RESOURCE_HINT_VALUE:
        raise JobAgentInputError(f"{name} exceeds the numeric limit")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise JobAgentInputError(f"{name} keys must be strings")
            _bounded_json(item, name, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _bounded_json(item, name, depth=depth + 1)


_CALLER_KEYS = frozenset({"execution_id", "session_id", "msg_id", "node_id"})
_DEFERRED_KEYS = frozenset({"target_head_id", "sender_session_id", "sender_msg_id"})
_CHAIN_KEYS = frozenset({"messages", "generations"})
_AUTHORITY_KEYS = frozenset({
    "speaker_kind", "speaker_id", "speaker_display", "principal_id",
    "authority_tier", "interaction",
})
_RESOURCE_HINT_KEYS = frozenset({
    "admission_id", "budget_scope_id", "effective_limits",
    "resolved_limits_snapshot", "caller_generations",
})
_JOB_CONTEXT_KEYS = frozenset({
    "parent_execution_id", "run_id", "branch_frontier", "caller",
    "worktree_id", "authority_snapshot", "deferred_inbox", "chain",
    "relation", "origin_turn_id", "resource_hints",
})
_OUTER_KEYS = frozenset({"version", "kind", "turn_request", "job_context"})


def _validate_context(value: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    context = _object(value, "job_context", _JOB_CONTEXT_KEYS)
    required = _JOB_CONTEXT_KEYS - set(context)
    if required:
        raise JobAgentInputError(f"job_context is missing fields: {sorted(required)}")
    for name in ("parent_execution_id", "run_id", "branch_frontier", "worktree_id", "origin_turn_id"):
        _optional_string(context[name], f"job_context.{name}")

    caller = context["caller"]
    if caller is not None:
        caller = _object(caller, "job_context.caller", _CALLER_KEYS)
        if set(caller) != _CALLER_KEYS or not all(isinstance(caller[key], str) and caller[key] for key in _CALLER_KEYS):
            raise JobAgentInputError("job_context.caller requires non-empty identity fields")
    context["caller"] = caller

    deferred = context["deferred_inbox"]
    if deferred is not None:
        deferred = _object(deferred, "job_context.deferred_inbox", _DEFERRED_KEYS)
        if set(deferred) != _DEFERRED_KEYS or not all(isinstance(deferred[key], str) and deferred[key] for key in _DEFERRED_KEYS):
            raise JobAgentInputError("job_context.deferred_inbox requires non-empty identity fields")
    context["deferred_inbox"] = deferred

    chain = _object(context["chain"], "job_context.chain", _CHAIN_KEYS)
    if set(chain) != _CHAIN_KEYS:
        raise JobAgentInputError("job_context.chain requires messages and generations")
    chain["messages"] = _bounded_int(chain["messages"], "job_context.chain.messages", MAX_JOB_CHAIN_VALUE)
    chain["generations"] = _bounded_int(chain["generations"], "job_context.chain.generations", MAX_JOB_CHAIN_VALUE)
    context["chain"] = chain
    if context["relation"] not in {"owned", "linked", "worktree"}:
        raise JobAgentInputError("job_context.relation is invalid")

    authority = _object(context["authority_snapshot"], "job_context.authority_snapshot", _AUTHORITY_KEYS)
    if set(authority) - _AUTHORITY_KEYS:
        raise JobAgentInputError("job_context.authority_snapshot has unknown fields")
    if authority and (set(authority) != _AUTHORITY_KEYS or not all(isinstance(authority[key], str) and authority[key] for key in _AUTHORITY_KEYS)):
        raise JobAgentInputError("job_context.authority_snapshot is incomplete")
    context["authority_snapshot"] = authority

    hints = _object(context["resource_hints"], "job_context.resource_hints", _RESOURCE_HINT_KEYS)
    if set(hints) != _RESOURCE_HINT_KEYS:
        raise JobAgentInputError("job_context.resource_hints is incomplete")
    for name in ("admission_id", "budget_scope_id"):
        _optional_string(hints[name], f"job_context.resource_hints.{name}")
    hints["caller_generations"] = _bounded_int(hints["caller_generations"], "job_context.resource_hints.caller_generations", MAX_JOB_CHAIN_VALUE)
    for name in ("effective_limits", "resolved_limits_snapshot"):
        if hints[name] is not None and not isinstance(hints[name], Mapping):
            raise JobAgentInputError(f"job_context.resource_hints.{name} must be an object or null")
        _bounded_json(hints[name], f"job_context.resource_hints.{name}")
    _json(hints)
    context["resource_hints"] = hints

    if caller is None:
        if request.get("spawn_caller") is not None or request.get("spawned_from_session") is not None:
            raise JobAgentInputError("spawn provenance requires job_context.caller")
    elif request.get("spawn_caller") != caller["node_id"] or request.get("spawned_from_session") != caller["session_id"]:
        raise JobAgentInputError("turn_request spawn provenance differs from job_context.caller")
    return context


def normalize_job_agent_input(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy and validate the complete Job-only durable input envelope."""
    outer = _object(payload, "job_agent input", _OUTER_KEYS)
    if set(outer) != _OUTER_KEYS or outer.get("version") != JOB_AGENT_INPUT_VERSION or outer.get("kind") != "job_agent":
        raise JobAgentInputError("unsupported Job Agent input")
    request = _object(outer["turn_request"], "turn_request", _turn_request_fields())
    for name in ("session_id", "user_text", "agent_id", "source"):
        if not isinstance(request.get(name), str) or not request[name]:
            raise JobAgentInputError(f"turn_request requires {name}")
    if "authority" in request:
        raise JobAgentInputError("turn_request.authority is not supported")
    outer["turn_request"] = request
    _bounded_json(request, "turn_request")
    outer["job_context"] = _validate_context(outer["job_context"], request)
    encoded = _json(outer)
    if len(encoded.encode("utf-8")) > MAX_JOB_AGENT_INPUT_BYTES:
        raise JobAgentInputError("Job Agent input exceeds the size limit")
    return json.loads(encoded)


def _turn_request_fields() -> frozenset[str]:
    from openprogram.agent.dispatcher.types import TurnRequest
    return frozenset(field.name for field in fields(TurnRequest))


@dataclass(frozen=True)
class JobAgentInputV1:
    """Validated version-1 input and conversion to the existing turn DTO."""

    turn_request: Mapping[str, Any]
    job_context: Mapping[str, Any]

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "JobAgentInputV1":
        value = normalize_job_agent_input(payload)
        return cls(value["turn_request"], value["job_context"])

    @classmethod
    def from_job(cls, job: Any, *, run_id: str | None = None) -> "JobAgentInputV1":
        from openprogram.agent.authority import normalize_authority

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
                "execution_id": job.parent_job_id or job.id,
                "session_id": caller_session,
                "msg_id": job.caller_msg_id,
                "node_id": job.caller_msg_id,
            }
        authority = normalize_authority(job)
        spawn_caller = job.spawn_caller if job.spawn_caller is not None else (
            caller["node_id"] if caller else None
        )
        request: dict[str, Any] = {
            "session_id": job.parent_session_id,
            "user_text": job.prompt,
            "agent_id": job.agent_id,
            "source": "job_runner",
            "permission_mode": "ask",
            "branch_from": job.parent_msg_id,
            "tools_override": copy.deepcopy(job.tools_override),
            "model_override": job.model_override,
            "thinking_effort": job.thinking_effort,
            "render_range": copy.deepcopy(job.render_range),
            "advance_head": job.advance_head,
            "spawn_caller": spawn_caller,
            "spawned_from_session": caller["session_id"] if caller else None,
            **authority,
        }
        payload = {
            "version": JOB_AGENT_INPUT_VERSION,
            "kind": "job_agent",
            "turn_request": request,
            "job_context": {
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
        return cls.parse(payload)

    def to_dict(self) -> dict[str, Any]:
        return normalize_job_agent_input({
            "version": JOB_AGENT_INPUT_VERSION,
            "kind": "job_agent",
            "turn_request": self.turn_request,
            "job_context": self.job_context,
        })

    def to_turn_request(self, *, session_id: str | None = None) -> Any:
        from openprogram.agent.dispatcher.types import TurnRequest

        values = copy.deepcopy(dict(self.turn_request))
        if session_id is not None and values.get("session_id") != session_id:
            raise JobAgentInputError("Job Agent input belongs to another session")
        return TurnRequest(**values)

    def to_job(self, *, execution_id: str, session_id: str) -> Any:
        """Rebuild the legacy Job projection from immutable Job input only."""
        from openprogram.agent.job.types import Job

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
            caller_chain_generations=hints["caller_generations"],
            spawn_caller=request.get("spawn_caller"),
            advance_head=bool(request.get("advance_head", False)),
            tools_override=copy.deepcopy(request.get("tools_override")),
            model_override=request.get("model_override"),
            thinking_effort=request.get("thinking_effort"),
            render_range=copy.deepcopy(request.get("render_range")),
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
