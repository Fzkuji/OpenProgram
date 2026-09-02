"""Durable Agent checkpoint payloads and resumable loop input.

The execution checkpoint manifest owns lifecycle identity.  This module owns
the one versioned Agent payload stored through that manifest.  It only accepts
canonical JSON and content-addressed state blobs, never a live provider or
tool object.
"""
from __future__ import annotations

import hashlib
import json
import marshal
from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

from openprogram.providers.types import AssistantMessage, ToolResultMessage

if TYPE_CHECKING:
    from openprogram.execution.checkpoints import CheckpointManifest
    from openprogram.execution.store import ExecutionStore
    from openprogram.agent.dispatcher.types import TurnRequest


AGENT_CHECKPOINT_SCHEMA_VERSION = 1
MAX_AGENT_CHECKPOINT_BYTES = 256 * 1024
MAX_AGENT_STATE_BLOB_BYTES = 1024 * 1024
MAX_AGENT_STATE_REFS = 32
MAX_AGENT_PENDING_MESSAGES = 64
MAX_AGENT_TERMINAL_EFFECT_RECEIPTS = 64
MAX_AGENT_DELTA_BYTES = 64 * 1024
MAX_AGENT_REPEAT_FAILURES = 16
_STATE_REF_PREFIX = "execstate://sha256/"
RUNTIME_CONTRACT_VERSION = 1


class AgentCheckpointError(ValueError):
    """A durable Agent checkpoint is malformed or exceeds its contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentCheckpointError(
            "checkpoint_schema_invalid", "checkpoint values must be JSON"
        ) from exc


def _json_safe(value: Any) -> Any:
    """Convert runtime metadata to canonical JSON without retaining objects."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    try:
        return json.loads(canonical_json_bytes(value))
    except AgentCheckpointError:
        return None


def _callable_descriptor(value: Any) -> dict[str, str] | None:
    if not callable(value):
        return None
    code = getattr(value, "__code__", None)
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if code is None or not isinstance(module, str) or not isinstance(qualname, str):
        return None
    try:
        code_material = marshal.dumps(code)
    except ValueError:
        return None
    return {
        "module": module,
        "qualname": qualname,
        "code_sha256": hashlib.sha256(code_material).hexdigest(),
    }


def _implementation_descriptor(tool: Any) -> dict[str, Any] | None:
    declared = getattr(tool, "_runtime_implementation", None)
    if isinstance(declared, Mapping):
        value = _json_safe(declared)
        return value if isinstance(value, dict) else None
    return _callable_descriptor(getattr(tool, "execute", None))


def runtime_contract_snapshot(
    *,
    model: Any,
    system_prompt: str,
    tools: list[Any] | tuple[Any, ...] | None,
    request: Any,
    structured_output: Any = None,
    toolset: Any = None,
) -> dict[str, Any]:
    """Build the immutable runtime contract used by continuation activation."""
    tool_values: list[dict[str, Any]] = []
    for tool in tools or ():
        implementation = _implementation_descriptor(tool)
        approval = getattr(tool, "_requires_approval", None)
        approval_value = approval if isinstance(approval, bool) or approval is None else _callable_descriptor(approval)
        if implementation is None:
            implementation_value = None
        else:
            implementation_value = implementation
        tool_values.append({
            "name": getattr(tool, "name", None),
            "description": getattr(tool, "description", None),
            "parameters": _json_safe(getattr(tool, "parameters", None)),
            "cache_control": _json_safe(getattr(tool, "cache_control", None)),
            "permission": {
                "mode": getattr(request, "permission_mode", None),
                "rules": _json_safe(getattr(request, "permission_rules", None)),
            },
            "approval": {
                "requires": approval_value,
                "accept_edits_safe": bool(getattr(tool, "_accept_edits_safe", False)),
            },
            "implementation": implementation_value,
        })
    request_semantics = {
        key: _json_safe(getattr(request, key, None))
        for key in (
            "thinking_effort", "service_tier", "tools_override", "response_format",
            "structured_output", "structured_output_mode", "structured_output_attempt",
            "additional_working_dirs", "_execution_revision_id",
        )
    }
    from openprogram.agent.internals._workdir import runtime_location_for
    request_semantics["execution_location"] = runtime_location_for(
        getattr(request, "session_id", ""),
    )
    model_value = _json_safe(model)
    if isinstance(model_value, dict) and "endpoint" not in model_value:
        model_value["endpoint"] = model_value.get("base_url")
    return {
        "contract_version": RUNTIME_CONTRACT_VERSION,
        "model": model_value,
        "system_prompt": system_prompt,
        "tools": tool_values,
        "structured_output": _json_safe(structured_output),
        "toolset": _json_safe(toolset),
        "request_semantics": request_semantics,
    }


def validate_runtime_contract(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> None:
    """Reject any runtime drift before a continuation can dispatch work."""
    required = {
        "contract_version", "model", "system_prompt", "tools",
        "structured_output", "toolset", "request_semantics",
    }
    if (
        not isinstance(expected, Mapping)
        or set(expected) != required
        or expected.get("contract_version") != RUNTIME_CONTRACT_VERSION
        or not isinstance(expected.get("model"), Mapping)
        or not isinstance(expected["model"].get("id"), str)
        or not expected["model"].get("id")
        or not all(
            isinstance(expected["model"].get(key), str)
            and expected["model"].get(key)
            for key in ("api", "provider", "base_url", "endpoint")
        )
        or not isinstance(expected.get("system_prompt"), str)
        or not isinstance(expected.get("tools"), list)
        or not isinstance(expected.get("request_semantics"), Mapping)
        or not isinstance(expected["request_semantics"].get("_execution_revision_id"), str)
        or not expected["request_semantics"]["_execution_revision_id"]
        or not isinstance(expected["request_semantics"].get("execution_location"), Mapping)
        or any(
            not isinstance(tool, Mapping)
            or set(tool) != {
                "name", "description", "parameters", "cache_control",
                "permission", "approval", "implementation",
            }
            or not isinstance(tool.get("name"), str)
            or not tool.get("name")
            or not isinstance(tool.get("description"), str)
            or not isinstance(tool.get("parameters"), Mapping)
            or not isinstance(tool.get("permission"), Mapping)
            or set(tool["permission"]) != {"mode", "rules"}
            or not isinstance(tool.get("approval"), Mapping)
            or set(tool["approval"]) != {"requires", "accept_edits_safe"}
            or not isinstance(tool["approval"].get("accept_edits_safe"), bool)
            or not isinstance(tool.get("implementation"), Mapping)
            or not all(
                isinstance(tool["implementation"].get(key), str)
                and tool["implementation"].get(key)
                for key in ("module", "qualname", "code_sha256")
            )
            for tool in expected["tools"]
        )
        or not isinstance(actual, Mapping)
        or dict(expected) != dict(actual)
    ):
        raise AgentCheckpointError(
            "continuation_contract_mismatch",
            "durable Agent runtime contract no longer resolves exactly",
        )


def _descriptor(payload: bytes, *, media_type: str = "application/json", schema_version: int = 1) -> dict[str, Any]:
    if len(payload) > MAX_AGENT_STATE_BLOB_BYTES:
        raise AgentCheckpointError("state_blob_too_large", "Agent state blob exceeds the size limit")
    if not media_type or type(schema_version) is not int or schema_version < 1:
        raise AgentCheckpointError("state_ref_invalid", "state blob media type and schema version are required")
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "ref": f"{_STATE_REF_PREFIX}{digest}",
        "sha256": digest,
        "byte_length": len(payload),
        "media_type": media_type,
        "schema_version": schema_version,
    }


def _validate_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "ref", "sha256", "byte_length", "media_type", "schema_version"
    }:
        raise AgentCheckpointError("state_ref_invalid", "state ref metadata is incomplete")
    ref = value["ref"]
    digest = value["sha256"]
    if (
        not isinstance(ref, str)
        or not isinstance(digest, str)
        or ref != f"{_STATE_REF_PREFIX}{digest}"
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or type(value["byte_length"]) is not int
        or value["byte_length"] < 0
        or value["byte_length"] > MAX_AGENT_STATE_BLOB_BYTES
        or value["media_type"] != "application/json"
        or value["schema_version"] != 1
    ):
        raise AgentCheckpointError("state_ref_invalid", "state ref metadata is invalid")
    return dict(value)


def _json_value(value: Any, *, name: str, cap: int | None = None) -> tuple[dict[str, Any], bytes]:
    payload = canonical_json_bytes(value)
    if cap is not None and len(payload) > cap:
        raise AgentCheckpointError("checkpoint_too_large", f"{name} exceeds its durable delta cap")
    return _descriptor(payload), payload


@dataclass(frozen=True)
class AgentCheckpointV1:
    """The only resumable Agent state payload.

    ``blob_payloads`` is deliberately kept outside ``payload``: it is the
    write-set for one SQLite transaction, while ``payload`` contains only
    immutable descriptors.  This prevents a checkpoint from retaining live
    Python objects or unbounded copies of media.
    """

    payload: Mapping[str, Any]
    blob_payloads: Mapping[str, bytes]

    @classmethod
    def build(
        cls,
        *,
        safe_point: Mapping[str, Any],
        frontier: list[Mapping[str, Any]],
        turn: Mapping[str, Any],
        assistant_message: Mapping[str, Any],
        tool_results: list[Mapping[str, Any]],
        resolved_snapshot: Mapping[str, Any],
        provider_action_id: str,
        tool_call_ids: list[str],
        next_tool_index: int,
        repeat_failures: Mapping[str, int],
        completed_actions: list[Mapping[str, Any]],
        terminal_effect_receipts: list[Mapping[str, Any]],
        pending_messages: list[Mapping[str, Any]] | None = None,
        pending_command_ids: list[str] | None = None,
    ) -> "AgentCheckpointV1":
        if safe_point.get("phase") not in {"after_provider", "after_tool"}:
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint phase is invalid")
        if not isinstance(next_tool_index, int) or next_tool_index < 0:
            raise AgentCheckpointError("checkpoint_schema_invalid", "next tool index is invalid")
        if next_tool_index > len(tool_call_ids):
            raise AgentCheckpointError("checkpoint_schema_invalid", "next tool index exceeds current decision")
        if len(tool_results) > MAX_AGENT_STATE_REFS:
            raise AgentCheckpointError("state_ref_limit", "too many tool result state refs")
        if len(repeat_failures) > MAX_AGENT_REPEAT_FAILURES or any(
            not isinstance(key, str) or type(value) is not int or value < 0
            for key, value in repeat_failures.items()
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "repeat failure state is invalid")
        pending_messages = list(pending_messages or [])
        if len(pending_messages) > MAX_AGENT_PENDING_MESSAGES:
            raise AgentCheckpointError("checkpoint_schema_invalid", "too many pending messages")
        if len(terminal_effect_receipts) > MAX_AGENT_TERMINAL_EFFECT_RECEIPTS:
            raise AgentCheckpointError("checkpoint_schema_invalid", "too many terminal effect receipts")
        if (
            not isinstance(resolved_snapshot, Mapping)
            or not isinstance(resolved_snapshot.get("model"), Mapping)
            or not isinstance(resolved_snapshot["model"].get("id"), str)
            or not resolved_snapshot["model"]["id"]
            or not isinstance(resolved_snapshot.get("system_prompt"), str)
            or not isinstance(resolved_snapshot.get("tools"), list)
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "resolved snapshot is invalid")
        validate_runtime_contract(resolved_snapshot, resolved_snapshot)

        blobs: dict[str, bytes] = {}
        refs: dict[str, dict[str, Any]] = {}

        def add(name: str, value: Any, *, cap: int | None = None) -> dict[str, Any]:
            descriptor, raw = _json_value(value, name=name, cap=cap)
            refs[name] = descriptor
            blobs[descriptor["ref"]] = raw
            return descriptor

        assistant_ref = add("assistant_message_delta", assistant_message, cap=MAX_AGENT_DELTA_BYTES)
        snapshot_ref = add("resolved_model_system_tool_snapshot", resolved_snapshot)
        tool_refs = [add(f"tool_result_delta.{index}", result, cap=MAX_AGENT_DELTA_BYTES) for index, result in enumerate(tool_results)]

        receipt_values: list[dict[str, Any]] = []
        for index, receipt in enumerate(terminal_effect_receipts):
            value = dict(receipt)
            terminal = value.pop("receipt", None)
            if terminal is None:
                terminal = value.pop("terminal_receipt", None)
            if terminal is None:
                raise AgentCheckpointError("checkpoint_schema_invalid", "terminal receipt payload is missing")
            receipt_ref = add(f"terminal_effect_receipt.{index}", terminal)
            value["receipt_ref"] = receipt_ref
            if value.get("outcome") not in {"committed", "not_committed", "compensated"}:
                raise AgentCheckpointError("checkpoint_schema_invalid", "terminal receipt outcome is invalid")
            if not all(isinstance(value.get(key), str) and value[key] for key in ("effect_id", "frontier_step_id", "action_id")):
                raise AgentCheckpointError("checkpoint_schema_invalid", "terminal receipt identity is invalid")
            receipt_values.append(value)

        pending_values: list[dict[str, Any]] = []
        for index, message in enumerate(pending_messages):
            value = dict(message)
            content = value.pop("content", None)
            if content is None:
                raise AgentCheckpointError("checkpoint_schema_invalid", "pending message content is missing")
            value["content_ref"] = add(
                f"pending_message.{index}", content, cap=MAX_AGENT_DELTA_BYTES,
            )
            if (
                not isinstance(value.get("message_id"), str)
                or type(value.get("sequence")) is not int
                or not isinstance(value.get("input_hash"), str)
            ):
                raise AgentCheckpointError("checkpoint_schema_invalid", "pending message identity is invalid")
            if value.get("status") not in {"pending", "applied", "rejected"}:
                raise AgentCheckpointError("checkpoint_schema_invalid", "pending message status is invalid")
            pending_values.append(value)

        if len(refs) > MAX_AGENT_STATE_REFS:
            raise AgentCheckpointError("state_ref_limit", "Agent checkpoint has too many state refs")
        actions: list[dict[str, Any]] = []
        tool_result_index = 0
        for item in completed_actions:
            if not isinstance(item, Mapping):
                raise AgentCheckpointError("checkpoint_schema_invalid", "completed action is invalid")
            action = dict(item)
            if not isinstance(action.get("action_id"), str) or not action["action_id"]:
                raise AgentCheckpointError("checkpoint_schema_invalid", "completed action identity is invalid")
            if not isinstance(action.get("input_hash"), str):
                raise AgentCheckpointError("checkpoint_schema_invalid", "completed action input hash is invalid")
            if action["action_id"] == provider_action_id:
                result_ref = assistant_ref
            elif tool_result_index < len(tool_refs):
                result_ref = tool_refs[tool_result_index]
                tool_result_index += 1
            else:
                raise AgentCheckpointError("checkpoint_schema_invalid", "completed action result ref is missing")
            action = {
                "action_id": action["action_id"],
                "input_hash": action["input_hash"],
                "result_ref": dict(result_ref),
            }
            actions.append(action)
        if tool_result_index != len(tool_refs):
            raise AgentCheckpointError("checkpoint_schema_invalid", "tool results have no completed action")
        payload = {
            "schema_version": AGENT_CHECKPOINT_SCHEMA_VERSION,
            "safe_point": dict(safe_point),
            "frontier": [dict(item) for item in frontier],
            "turn": dict(turn),
            "state_refs": refs,
            "pending_messages": pending_values,
            "assistant_message_delta_ref": assistant_ref,
            "tool_result_delta_refs": tool_refs,
            "current_decision": {
                "provider_action_id": provider_action_id,
                "assistant_message_ref": assistant_ref,
                "tool_call_ids": list(tool_call_ids),
            },
            "next_tool_index": next_tool_index,
            "repeat_failures": dict(repeat_failures),
            "resolved_model_system_tool_snapshot_ref": snapshot_ref,
            "completed_actions": actions,
            "terminal_effect_receipts": receipt_values,
            "pending_command_ids": list(pending_command_ids or []),
        }
        checkpoint = cls(payload=payload, blob_payloads=blobs)
        checkpoint.validate()
        if len(checkpoint.to_bytes()) > MAX_AGENT_CHECKPOINT_BYTES:
            raise AgentCheckpointError("checkpoint_too_large", "Agent checkpoint exceeds the size limit")
        return checkpoint

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)

    def validate(self) -> None:
        value = self.payload
        required = {
            "schema_version", "safe_point", "frontier", "turn", "state_refs",
            "pending_messages", "assistant_message_delta_ref", "tool_result_delta_refs",
            "current_decision", "next_tool_index", "repeat_failures",
            "resolved_model_system_tool_snapshot_ref", "completed_actions",
            "terminal_effect_receipts", "pending_command_ids",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != required
            or value.get("schema_version") != AGENT_CHECKPOINT_SCHEMA_VERSION
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "unsupported Agent checkpoint schema")
        if len(self.to_bytes()) > MAX_AGENT_CHECKPOINT_BYTES:
            raise AgentCheckpointError("checkpoint_too_large", "Agent checkpoint exceeds the size limit")
        safe_point = value["safe_point"]
        if (
            not isinstance(safe_point, Mapping)
            or set(safe_point) != {"kind", "step_id", "phase", "sentinel"}
            or safe_point.get("phase") not in {"after_provider", "after_tool"}
            or not isinstance(safe_point.get("step_id"), str)
            or not safe_point["step_id"]
            or safe_point.get("sentinel") != "resume-from-checkpoint"
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint safe point is invalid")
        expected_kind = (
            "agent.provider.decision.after"
            if safe_point["phase"] == "after_provider"
            else "agent.tool.action.after"
        )
        if safe_point.get("kind") != expected_kind:
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint safe point kind is invalid")
        frontier = value["frontier"]
        if (
            not isinstance(frontier, list)
            or not frontier
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"step_id", "phase", "branch_id"}
                or item.get("step_id") != safe_point["step_id"]
                or item.get("phase") != safe_point["phase"]
                or not isinstance(item.get("branch_id"), str)
                or not item["branch_id"]
                for item in frontier
            )
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint frontier is invalid")
        turn = value["turn"]
        if (
            not isinstance(turn, Mapping)
            or set(turn) != {"user_message_id", "assistant_message_id", "base_history_head_id"}
            or not all(
                isinstance(turn.get(key), str) and turn[key]
                for key in ("user_message_id", "assistant_message_id", "base_history_head_id")
            )
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint branch anchors are invalid")
        refs = value["state_refs"]
        if not isinstance(refs, Mapping) or len(refs) > MAX_AGENT_STATE_REFS:
            raise AgentCheckpointError("state_ref_limit", "checkpoint state refs are invalid")
        if any(not isinstance(name, str) or not name for name in refs):
            raise AgentCheckpointError("state_ref_invalid", "checkpoint state ref names are invalid")
        for descriptor in refs.values():
            checked = _validate_descriptor(descriptor)
            if self.blob_payloads:
                raw = self.blob_payloads.get(checked["ref"])
                if raw is None or _descriptor(
                    raw,
                    media_type=checked["media_type"],
                    schema_version=checked["schema_version"],
                ) != checked:
                    raise AgentCheckpointError("state_ref_invalid", "checkpoint blob metadata differs from payload")
        tool_refs = value["tool_result_delta_refs"]
        if not isinstance(tool_refs, list) or len(tool_refs) > MAX_AGENT_STATE_REFS:
            raise AgentCheckpointError("checkpoint_schema_invalid", "tool result refs are invalid")
        if (
            not isinstance(value["pending_messages"], list)
            or not isinstance(value["terminal_effect_receipts"], list)
            or len(value["pending_messages"]) > MAX_AGENT_PENDING_MESSAGES
            or len(value["terminal_effect_receipts"]) > MAX_AGENT_TERMINAL_EFFECT_RECEIPTS
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint collection exceeds its cap")
        expected_ref_names = {
            "assistant_message_delta",
            "resolved_model_system_tool_snapshot",
            *(f"tool_result_delta.{index}" for index in range(len(tool_refs))),
            *(f"terminal_effect_receipt.{index}" for index in range(len(value["terminal_effect_receipts"]))),
            *(f"pending_message.{index}" for index in range(len(value["pending_messages"]))),
        }
        if set(refs) != expected_ref_names:
            raise AgentCheckpointError("state_ref_invalid", "checkpoint state refs do not own every durable value")
        fixed_refs = [
            ("assistant_message_delta", value["assistant_message_delta_ref"]),
            ("resolved_model_system_tool_snapshot", value["resolved_model_system_tool_snapshot_ref"]),
            *[(f"tool_result_delta.{index}", descriptor) for index, descriptor in enumerate(tool_refs)],
        ]
        for name, descriptor in fixed_refs:
            checked = _validate_descriptor(descriptor)
            if refs[name] != checked:
                raise AgentCheckpointError("state_ref_invalid", "checkpoint state ref does not match its field")
            if checked["ref"] not in self.blob_payloads and self.blob_payloads:
                raise AgentCheckpointError("state_ref_invalid", "checkpoint blob payload is missing")
        decision = value["current_decision"]
        if (
            not isinstance(decision, Mapping)
            or set(decision) != {"provider_action_id", "assistant_message_ref", "tool_call_ids"}
            or not isinstance(decision.get("provider_action_id"), str)
            or not decision["provider_action_id"]
            or not isinstance(decision.get("tool_call_ids"), list)
            or any(not isinstance(item, str) or not item for item in decision["tool_call_ids"])
            or len(set(decision["tool_call_ids"])) != len(decision["tool_call_ids"])
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "current decision is invalid")
        if decision["assistant_message_ref"] != value["assistant_message_delta_ref"]:
            raise AgentCheckpointError("state_ref_invalid", "decision assistant ref differs from the assistant delta")
        if (
            type(value["next_tool_index"]) is not int
            or value["next_tool_index"] < 0
            or value["next_tool_index"] > len(decision["tool_call_ids"])
            or len(tool_refs) != value["next_tool_index"]
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "next tool index exceeds current decision")
        snapshot = value["resolved_model_system_tool_snapshot_ref"]
        if snapshot != refs["resolved_model_system_tool_snapshot"]:
            raise AgentCheckpointError("state_ref_invalid", "resolved snapshot ref differs from state refs")
        for index, pending in enumerate(value["pending_messages"]):
            if (
                not isinstance(pending, Mapping)
                or set(pending) != {"message_id", "sequence", "input_hash", "status", "content_ref"}
                or not isinstance(pending.get("message_id"), str)
                or type(pending.get("sequence")) is not int
                or not isinstance(pending.get("input_hash"), str)
                or pending.get("status") not in {"pending", "applied", "rejected"}
                or pending.get("content_ref") != refs[f"pending_message.{index}"]
            ):
                raise AgentCheckpointError("checkpoint_schema_invalid", "pending message is invalid")
        receipt_action_ids: set[str] = set()
        for index, receipt in enumerate(value["terminal_effect_receipts"]):
            if (
                not isinstance(receipt, Mapping)
                or set(receipt) != {"effect_id", "frontier_step_id", "action_id", "outcome", "receipt_ref"}
                or not all(isinstance(receipt.get(key), str) and receipt[key] for key in ("effect_id", "frontier_step_id", "action_id"))
                or receipt.get("outcome") not in {"committed", "not_committed", "compensated"}
                or receipt.get("receipt_ref") != refs[f"terminal_effect_receipt.{index}"]
                or receipt["action_id"] in receipt_action_ids
            ):
                raise AgentCheckpointError("checkpoint_schema_invalid", "terminal effect receipt is invalid")
            receipt_action_ids.add(receipt["action_id"])
        actions = value["completed_actions"]
        if not isinstance(actions, list) or not actions:
            raise AgentCheckpointError("checkpoint_schema_invalid", "completed actions are invalid")
        action_ids: set[str] = set()
        for action in actions:
            if (
                not isinstance(action, Mapping)
                or set(action) != {"action_id", "input_hash", "result_ref"}
                or not isinstance(action.get("action_id"), str)
                or not action["action_id"]
                or not isinstance(action.get("input_hash"), str)
                or action["action_id"] in action_ids
            ):
                raise AgentCheckpointError("checkpoint_schema_invalid", "completed action is invalid")
            result_ref = _validate_descriptor(action.get("result_ref"))
            if result_ref["ref"] not in {descriptor["ref"] for descriptor in refs.values()}:
                raise AgentCheckpointError("state_ref_invalid", "completed action result ref is not owned by this checkpoint")
            action_ids.add(action["action_id"])
        if action_ids != receipt_action_ids:
            raise AgentCheckpointError("checkpoint_schema_invalid", "actions and terminal receipts differ")
        if (
            not isinstance(value["repeat_failures"], Mapping)
            or len(value["repeat_failures"]) > MAX_AGENT_REPEAT_FAILURES
            or any(
                not isinstance(key, str) or type(count) is not int or count < 0
                for key, count in value["repeat_failures"].items()
            )
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "too many repeat failures")
        if (
            not isinstance(value["pending_command_ids"], list)
            or len(value["pending_command_ids"]) > MAX_AGENT_PENDING_MESSAGES
            or any(not isinstance(item, str) or not item for item in value["pending_command_ids"])
            or len(set(value["pending_command_ids"])) != len(value["pending_command_ids"])
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "pending command ids are invalid")

    @classmethod
    def load(cls, store: "ExecutionStore", checkpoint: "CheckpointManifest") -> "AgentCheckpointV1":
        descriptor = checkpoint.state_refs.get("agent_checkpoint")
        descriptor = _validate_descriptor(descriptor)
        try:
            blob = store.get_state_blob(checkpoint.execution_id, descriptor["ref"])
        except Exception as exc:
            raise AgentCheckpointError(
                getattr(exc, "code", "state_blob_corrupt"), str(exc)
            ) from exc
        if blob is None:
            raise AgentCheckpointError("state_ref_invalid", "Agent checkpoint state blob is missing")
        if (
            blob["sha256"] != descriptor["sha256"]
            or blob["byte_length"] != descriptor["byte_length"]
            or blob["media_type"] != descriptor["media_type"]
            or blob["schema_version"] != descriptor["schema_version"]
        ):
            raise AgentCheckpointError("state_blob_corrupt", "Agent checkpoint state blob metadata changed")
        try:
            payload = json.loads(blob["payload"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentCheckpointError("checkpoint_schema_invalid", "Agent checkpoint payload is not UTF-8 JSON") from exc
        value = cls(payload=payload, blob_payloads={})
        value.validate()
        for descriptor in value.payload["state_refs"].values():
            checked = _validate_descriptor(descriptor)
            try:
                referenced = store.get_state_blob(checkpoint.execution_id, checked["ref"])
            except Exception as exc:
                raise AgentCheckpointError(
                    getattr(exc, "code", "state_blob_corrupt"), str(exc)
                ) from exc
            if (
                referenced is None
                or referenced["sha256"] != checked["sha256"]
                or referenced["byte_length"] != checked["byte_length"]
                or referenced["media_type"] != checked["media_type"]
                or referenced["schema_version"] != checked["schema_version"]
            ):
                raise AgentCheckpointError(
                    "state_blob_corrupt", "Agent checkpoint references a missing or foreign state blob"
                )
            try:
                json.loads(referenced["payload"].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AgentCheckpointError(
                    "checkpoint_schema_invalid", "Agent checkpoint state blob is not UTF-8 JSON"
                ) from exc
        snapshot = value.read_json_ref(
            store, checkpoint.execution_id,
            value.payload["resolved_model_system_tool_snapshot_ref"],
        )
        validate_runtime_contract(snapshot, snapshot)
        return value

    def read_json_ref(self, store: "ExecutionStore", execution_id: str, descriptor: Mapping[str, Any]) -> Any:
        checked = _validate_descriptor(descriptor)
        try:
            blob = store.get_state_blob(execution_id, checked["ref"])
        except Exception as exc:
            raise AgentCheckpointError(
                getattr(exc, "code", "state_blob_corrupt"), str(exc)
            ) from exc
        if blob is None or blob["sha256"] != checked["sha256"] or blob["byte_length"] != checked["byte_length"] or blob["media_type"] != checked["media_type"] or blob["schema_version"] != checked["schema_version"]:
            raise AgentCheckpointError("state_blob_corrupt", "Agent state reference is missing or corrupt")
        try:
            return json.loads(blob["payload"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentCheckpointError("checkpoint_schema_invalid", "Agent state blob is not JSON") from exc


@dataclass(frozen=True)
class AgentContinuation:
    """A fresh-attempt carrier reconstructed solely from a checkpoint."""

    request: "TurnRequest"
    checkpoint: "CheckpointManifest"
    state: AgentCheckpointV1
    assistant_message: AssistantMessage
    tool_results: tuple[ToolResultMessage, ...]
    resolved_snapshot: Mapping[str, Any]

    @classmethod
    def from_checkpoint(
        cls, *, store: "ExecutionStore", checkpoint: "CheckpointManifest", request: "TurnRequest"
    ) -> "AgentContinuation":
        state = AgentCheckpointV1.load(store, checkpoint)
        payload = state.payload
        turn = payload["turn"]
        if request.user_msg_id and request.user_msg_id != turn["user_message_id"]:
            raise AgentCheckpointError("checkpoint_schema_invalid", "continuation request has a different user anchor")
        request.user_msg_id = turn["user_message_id"]
        assistant_data = state.read_json_ref(
            store, checkpoint.execution_id, payload["assistant_message_delta_ref"]
        )
        try:
            assistant = AssistantMessage.model_validate(assistant_data)
            results = tuple(
                ToolResultMessage.model_validate(
                    state.read_json_ref(store, checkpoint.execution_id, descriptor)
                )
                for descriptor in payload["tool_result_delta_refs"]
            )
        except Exception as exc:
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint message delta is invalid") from exc
        tool_call_ids = [item.id for item in assistant.content if getattr(item, "type", None) == "toolCall"]
        if tool_call_ids != payload["current_decision"]["tool_call_ids"]:
            raise AgentCheckpointError("checkpoint_schema_invalid", "checkpoint decision tool calls differ from assistant delta")
        snapshot = state.read_json_ref(
            store, checkpoint.execution_id, payload["resolved_model_system_tool_snapshot_ref"]
        )
        if (
            not isinstance(snapshot, Mapping)
            or not isinstance(snapshot.get("model"), Mapping)
            or not isinstance(snapshot["model"].get("id"), str)
            or not snapshot["model"]["id"]
            or not isinstance(snapshot.get("system_prompt"), str)
            or not isinstance(snapshot.get("tools"), list)
        ):
            raise AgentCheckpointError("checkpoint_schema_invalid", "resolved snapshot is invalid")
        validate_runtime_contract(snapshot, snapshot)
        return cls(
            request=request,
            checkpoint=checkpoint,
            state=state,
            assistant_message=assistant,
            tool_results=results,
            resolved_snapshot=dict(snapshot),
        )

    @property
    def phase(self) -> str:
        return str(self.state.payload["safe_point"]["phase"])

    @property
    def assistant_message_id(self) -> str:
        return str(self.state.payload["turn"]["assistant_message_id"])

    @property
    def provider_action_id(self) -> str:
        return str(self.state.payload["current_decision"]["provider_action_id"])

    @property
    def next_tool_index(self) -> int:
        return int(self.state.payload["next_tool_index"])

    @property
    def repeat_failures(self) -> dict[str, int]:
        return dict(self.state.payload["repeat_failures"])
