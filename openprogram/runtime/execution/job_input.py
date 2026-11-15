"""The strict, versioned admission input of a background Agent Job.

The execution store keeps this envelope immutable beside a Job's canonical
execution and validates it on admission and whenever it reads Job
provenance. Which fields a turn request may carry comes from the turn
engine through the ``turn_request_fields`` hook; converting the envelope to
and from a live Job or turn request belongs to ``openprogram.runtime.jobs``.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Mapping

from . import hooks


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
    context = _object(value, "job_context", _JOB_CONTEXT_KEYS | {"usage_goal", "tool_call_id"})
    required = _JOB_CONTEXT_KEYS - set(context)
    if required:
        raise JobAgentInputError(f"job_context is missing fields: {sorted(required)}")
    if context.get("usage_goal") is not None:
        from openprogram.models.usage.context import GOAL_FIELDS
        identity = _object(context["usage_goal"], "job_context.usage_goal", GOAL_FIELDS)
        if set(identity) != GOAL_FIELDS:
            raise JobAgentInputError("job_context.usage_goal is incomplete")
        for key in GOAL_FIELDS - {"goal_revision"}:
            if not isinstance(identity[key], str) or not identity[key] or len(identity[key]) > 512:
                raise JobAgentInputError("job_context.usage_goal has invalid identity")
        if type(identity["goal_revision"]) is not int or not 1 <= identity["goal_revision"] <= MAX_RESOURCE_HINT_VALUE:
            raise JobAgentInputError("job_context.usage_goal has invalid revision")
        context["usage_goal"] = identity
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

    chain = _object(context["chain"], "job_context.chain", _CHAIN_KEYS | {"ancestors"})
    if not _CHAIN_KEYS <= set(chain):
        raise JobAgentInputError("job_context.chain requires messages and generations")
    chain["messages"] = _bounded_int(chain["messages"], "job_context.chain.messages", MAX_JOB_CHAIN_VALUE)
    chain["generations"] = _bounded_int(chain["generations"], "job_context.chain.generations", MAX_JOB_CHAIN_VALUE)
    if "ancestors" in chain:
        # Who waits above a delivered message (send_message's cycle check);
        # present only when a chain has any, so other inputs keep their hash.
        ancestors = chain["ancestors"]
        if (not isinstance(ancestors, list) or not ancestors or len(ancestors) > 32
                or not all(isinstance(a, str) and a and len(a) <= 512 for a in ancestors)):
            raise JobAgentInputError("job_context.chain.ancestors must be a short list of addresses")
        chain["ancestors"] = list(ancestors)
    context["chain"] = chain
    if "tool_call_id" in context:
        # The model call that started this Job, so a card rebuilt after a
        # crash pairs with it. Present only when there is one.
        _optional_string(context["tool_call_id"], "job_context.tool_call_id")
        if not context["tool_call_id"]:
            raise JobAgentInputError("job_context.tool_call_id must be non-empty when present")
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
    request = _object(outer["turn_request"], "turn_request", hooks.turn_request_fields())
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


@dataclass(frozen=True)
class JobAgentInputV1:
    """Validated version-1 Job input."""

    turn_request: Mapping[str, Any]
    job_context: Mapping[str, Any]

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "JobAgentInputV1":
        value = normalize_job_agent_input(payload)
        return cls(value["turn_request"], value["job_context"])

    def to_dict(self) -> dict[str, Any]:
        return normalize_job_agent_input({
            "version": JOB_AGENT_INPUT_VERSION,
            "kind": "job_agent",
            "turn_request": self.turn_request,
            "job_context": self.job_context,
        })
