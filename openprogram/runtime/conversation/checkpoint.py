"""Resuming an Agent turn from its journal.

This module holds the runtime contract snapshot taken from live tools and the
turn request, and the continuation that turns a journal position back into
provider messages for a fresh attempt.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from types import CodeType
from typing import Any, Mapping, TYPE_CHECKING

from openprogram.models.providers.types import (
    AssistantMessage, TextContent, ToolResultMessage, UserMessage,
)
from openprogram.runtime.execution.agent_journal import (
    INTERRUPTED_TOOL_TEXT, JournalState, interrupted_outcome, load_journal,
)
from openprogram.runtime.execution.runtime_contract import (
    RUNTIME_CONTRACT_VERSION,
    RuntimeContractError,
    contract_sha256,
    json_safe as _json_safe,
    validate_runtime_contract,
)

if TYPE_CHECKING:
    from openprogram.runtime.execution.checkpoints import CheckpointManifest
    from openprogram.runtime.execution.store import ExecutionStore
    from openprogram.runtime.agent.turn_request import TurnRequest


def _code_material(value: Any) -> bytes:
    """What a code object does, in bytes that depend on nothing else.

    ``marshal.dumps`` is not that: it marks objects shared by reference only
    when something else holds them too, so the same function hashes
    differently as the process runs, and sets come out in hash-seed order.
    A continuation compared those bytes and refused tools that had not
    changed. Source location (file name, line numbers) is left out as well;
    moving a function does not change what it does.
    """
    if isinstance(value, CodeType):
        parts = [
            b"code", value.co_code,
            *(str(getattr(value, name)).encode() for name in (
                "co_argcount", "co_posonlyargcount", "co_kwonlyargcount",
                "co_flags", "co_names", "co_varnames", "co_freevars", "co_cellvars",
            )),
            *(_code_material(item) for item in value.co_consts),
        ]
    elif isinstance(value, (tuple, list)):
        parts = [type(value).__name__.encode(), *(_code_material(item) for item in value)]
    elif isinstance(value, (frozenset, set)):
        parts = [b"set", *sorted(_code_material(item) for item in value)]
    else:
        parts = [type(value).__name__.encode(), repr(value).encode("utf-8", "surrogatepass")]
    return b"".join(hashlib.sha256(part).digest() for part in parts)


def _callable_descriptor(value: Any) -> dict[str, str] | None:
    if not callable(value):
        return None
    code = getattr(value, "__code__", None)
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not isinstance(code, CodeType) or not isinstance(module, str) or not isinstance(qualname, str):
        return None
    return {
        "module": module,
        "qualname": qualname,
        "code_sha256": hashlib.sha256(_code_material(code)).hexdigest(),
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
    """Build the immutable runtime contract used by continuation activation.

    A tool is named with two digests, not copied: a resume resolves the live
    tools again and only has to tell whether each is still the one the turn
    saw. ``declaration_sha256`` covers what the model and the permission
    check were given (description, parameters, cache control, permission,
    approval), ``implementation_sha256`` the code that runs. A digest is
    None for a tool that cannot be resumed against (no schema, no
    identifiable code), which ``validate_runtime_contract`` rejects.
    """
    permission = {
        "mode": getattr(request, "permission_mode", None),
        "rules": _json_safe(getattr(request, "permission_rules", None)),
    }
    tool_values: list[dict[str, Any]] = []
    for tool in tools or ():
        implementation = _implementation_descriptor(tool)
        approval = getattr(tool, "_requires_approval", None)
        description = getattr(tool, "description", None)
        parameters = _json_safe(getattr(tool, "parameters", None))
        declared = isinstance(description, str) and isinstance(parameters, Mapping)
        identified = isinstance(implementation, Mapping) and all(
            isinstance(implementation.get(key), str) and implementation.get(key)
            for key in ("module", "qualname", "code_sha256")
        )
        tool_values.append({
            "name": getattr(tool, "name", None),
            "declaration_sha256": contract_sha256({
                "description": description,
                "parameters": parameters,
                "cache_control": _json_safe(getattr(tool, "cache_control", None)),
                "permission": permission,
                "approval": {
                    "requires": (approval if isinstance(approval, bool) or approval is None
                                 else _callable_descriptor(approval)),
                    "accept_edits_safe": bool(getattr(tool, "_accept_edits_safe", False)),
                },
            }) if declared else None,
            "implementation_sha256": contract_sha256(implementation) if identified else None,
        })
    request_semantics = {
        key: _json_safe(getattr(request, key, None))
        for key in (
            "thinking_effort", "service_tier", "tools_override", "response_format",
            "structured_output", "structured_output_mode", "structured_output_attempt",
            "additional_working_dirs", "_execution_revision_id",
        )
    }
    from openprogram.runtime.conversation.workdir import runtime_location_for
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


def _interrupted_result(step: Any) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=step.tool_call_id, tool_name=step.tool_name,
        content=[TextContent(text=INTERRUPTED_TOOL_TEXT)], is_error=True,
        timestamp=int(time.time() * 1000),
    )


@dataclass(frozen=True)
class AgentContinuation:
    """A fresh attempt's starting point, rebuilt from the turn's journal."""

    request: "TurnRequest"
    checkpoint: "CheckpointManifest"
    journal: JournalState
    pointer: Mapping[str, Any]
    user_message_id: str
    assistant_message_id: str
    assistant_message: AssistantMessage
    tool_results: tuple[ToolResultMessage, ...]
    resolved_snapshot: Mapping[str, Any]
    display: tuple[dict[str, Any], ...] = ()
    # A tool that started and never reported back, when it is not safe to
    # repeat: this result tells the model so, and the first owner records it.
    interrupted_result: ToolResultMessage | None = None
    execution_store: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_checkpoint(
        cls, *, store: "ExecutionStore", checkpoint: "CheckpointManifest", request: "TurnRequest",
        sessions: Any = None,
    ) -> "AgentContinuation":
        pointer = checkpoint.state_refs.get("agent_journal")
        if not isinstance(pointer, Mapping):
            raise RuntimeContractError("checkpoint_schema_invalid", "checkpoint names no journal position")
        state = load_journal(request.session_id, pointer, sessions=sessions)
        decision = state.last
        if decision is None:
            raise RuntimeContractError("checkpoint_schema_invalid", "journal has no model decision")
        turn = state.turn
        if request.user_msg_id and request.user_msg_id != turn.get("user_message_id"):
            raise RuntimeContractError("checkpoint_schema_invalid", "continuation request has a different user anchor")
        request.user_msg_id = turn["user_message_id"]
        snapshot = state.snapshot
        if (
            not isinstance(snapshot.get("model"), Mapping)
            or not isinstance(snapshot["model"].get("id"), str)
            or not snapshot["model"]["id"]
            # The prompt's text, or the hash of the prompt recorded on the
            # branch (``loop_runner.prompt_by_reference``).
            or not isinstance(snapshot.get("system_prompt", snapshot.get("system_prompt_sha256")), str)
            or not isinstance(snapshot.get("tools"), list)
        ):
            raise RuntimeContractError("checkpoint_schema_invalid", "resolved snapshot is invalid")
        validate_runtime_contract(snapshot, snapshot)
        try:
            assistant = AssistantMessage.model_validate(decision.message)
            results = [ToolResultMessage.model_validate(item) for item in state.tool_results()]
        except Exception as exc:
            raise RuntimeContractError("checkpoint_schema_invalid", "journal message is invalid") from exc
        interrupted = None
        unfinished = state.interrupted_step()
        if unfinished is not None and interrupted_outcome(unfinished[1]) == "report":
            interrupted = _interrupted_result(unfinished[1])
            results.append(interrupted)
        return cls(
            request=request, checkpoint=checkpoint, journal=state, pointer=dict(pointer),
            user_message_id=str(turn["user_message_id"]),
            assistant_message_id=str(turn["assistant_message_id"]),
            assistant_message=assistant, tool_results=tuple(results),
            resolved_snapshot=dict(snapshot),
            display=tuple(state.display_blocks()),
            interrupted_result=interrupted,
            execution_store=store,
        )

    @property
    def phase(self) -> str:
        return "after_tool" if self.tool_results else "after_provider"

    @property
    def provider_action_id(self) -> str:
        return self.journal.last.action_id

    @property
    def next_tool_index(self) -> int:
        return len(self.tool_results)

    @property
    def repeat_failures(self) -> dict[str, int]:
        return dict(self.journal.repeat_failures)

    @property
    def loaded_deferred_tools(self) -> list[str]:
        return list(self.journal.loaded_deferred_tools)

    def prior_messages(self) -> list[Any]:
        """Everything this turn said before its current decision, in order."""
        messages: list[Any] = [_steer_message(item) for item in self.journal.steers_before]
        for decision in self.journal.decisions[:-1]:
            messages.append(AssistantMessage.model_validate(decision.message))
            for call in decision.tool_calls:
                step = decision.steps.get(str(call.get("id")))
                if step is not None and step.result is not None:
                    messages.append(ToolResultMessage.model_validate(step.result))
            messages.extend(_steer_message(item) for item in decision.steers_after)
        return messages

    def steer_messages(self) -> list[Any]:
        """Steering delivered after the current decision's tools."""
        return [_steer_message(item) for item in self.journal.last.steers_after]


def _steer_message(record: Mapping[str, Any]) -> UserMessage:
    return UserMessage.model_validate(record["message"])
