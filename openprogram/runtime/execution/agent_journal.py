"""What an Agent turn has done, as lines in its session's log.

A turn appends one line for each model decision, each tool it starts and each
tool result. Nothing else is written while it runs, and resuming a turn reads
these lines back. A checkpoint is only a pointer to a position here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

AGENT_STATE_SCHEMA_VERSION = 1

DECISION = "turn.decision"
SNAPSHOT = "turn.snapshot"
TOOL_STARTED = "turn.tool_started"
TOOL_RESULT = "turn.tool_result"
TOOL_SUSPENDED = "turn.tool_suspended"
STEER = "turn.steer"

INTERRUPTED_TOOL_TEXT = (
    "This tool call was interrupted and it is unknown whether it finished. "
    "Check the current state before deciding what to do next."
)
_READ_ONLY_TOOLS = frozenset({
    "read", "glob", "grep", "list", "lsp_definition", "lsp_diagnostics", "lsp_references",
    "semble_search", "semble_find_related", "read_conversation", "read_mcp_resource",
    "list_mcp_resources", "list_mcp_prompts", "get_mcp_prompt", "list_agents", "list_jobs",
    "job_output", "todo_list", "get_goal", "help",
})
_FILE_EDIT_TOOLS = frozenset({"write", "edit"})


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_digest(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def _edit_target(arguments: Mapping[str, Any]) -> str | None:
    """The local file an edit will touch; None when it cannot be compared here."""
    value = arguments.get("file_path") or arguments.get("path")
    if not isinstance(value, str) or not value:
        return None
    from openprogram.host.environments.remote import remote_fs
    from openprogram.state.worktree.path_resolve import resolve_path

    if remote_fs() is not None:
        return None
    return resolve_path(value)[0]


def _sessions():
    from openprogram.runtime.context.session_db import default_db

    return default_db()


class AgentJournal:
    """Appends one execution's records and keeps their fold in ``state``."""

    def __init__(self, session_id: str, execution_id: str, *, state: "JournalState | None" = None,
                 position: int = 0, source: Mapping[str, Any] | None = None, sessions=None):
        self.session_id = session_id
        self.execution_id = execution_id
        self.position = position
        self.source = dict(source) if source else None
        self.state = state if state is not None else JournalState(execution_id=execution_id)
        self._sessions = sessions
        self._snapshot_digest = _digest(self.state.snapshot) if self.state.snapshot else None

    def _append(self, kind: str, data: Mapping[str, Any]) -> None:
        self.position += 1
        record = {"execution_id": self.execution_id, "position": self.position, **data}
        sessions = self._sessions if self._sessions is not None else _sessions()
        sessions.append_turn_record(self.session_id, kind, record)
        apply_journal_record(self.state, {"type": kind, "data": record})

    def record_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        digest = _digest(snapshot)
        if digest != self._snapshot_digest:
            self._snapshot_digest = digest
            self._append(SNAPSHOT, {"snapshot": dict(snapshot)})

    def record_decision(self, message: Mapping[str, Any], *, action_id: str, turn: Mapping[str, Any],
                        loaded_deferred_tools=()) -> None:
        self._append(DECISION, {
            "message": dict(message), "action_id": action_id, "turn": dict(turn),
            "loaded_deferred_tools": list(loaded_deferred_tools),
        })

    def record_tool_started(self, tool_call_id: str, tool_name: str, arguments: Mapping[str, Any]) -> None:
        data: dict[str, Any] = {"tool_call_id": tool_call_id, "tool_name": tool_name}
        if tool_name in _FILE_EDIT_TOOLS and (target := _edit_target(arguments)):
            data.update(path=target, file_digest=_file_digest(target))
        self._append(TOOL_STARTED, data)

    def record_tool_result(self, tool_call_id: str, result: Mapping[str, Any], *, is_error: bool,
                           outcome: str | None, repeat_failures: Mapping[str, int],
                           loaded_deferred_tools=()) -> None:
        self._append(TOOL_RESULT, {
            "tool_call_id": tool_call_id, "result": dict(result), "is_error": is_error, "outcome": outcome,
            "repeat_failures": dict(repeat_failures), "loaded_deferred_tools": list(loaded_deferred_tools),
        })

    def record_tool_suspended(self, tool_call_id: str) -> None:
        """The tool stopped at a durable boundary and resumes by running again."""
        self._append(TOOL_SUSPENDED, {"tool_call_id": tool_call_id})

    def record_steer(self, command_id: str, message_id: str, message: Mapping[str, Any]) -> None:
        self._append(STEER, {"command_id": command_id, "message_id": message_id, "message": dict(message)})

    def pointer(self) -> dict[str, Any]:
        value: dict[str, Any] = {"execution_id": self.execution_id, "position": self.position}
        if self.source:
            value["source"] = dict(self.source)
        return value


@dataclass
class ToolStep:
    tool_call_id: str
    tool_name: str = ""
    started: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    is_error: bool = False
    outcome: str | None = None


@dataclass
class Decision:
    action_id: str
    message: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    steps: dict[str, ToolStep] = field(default_factory=dict)
    steers_after: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class JournalState:
    """One turn folded from its records."""

    execution_id: str = ""
    position: int = 0
    turn: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    steers_before: list[dict[str, Any]] = field(default_factory=list)
    repeat_failures: dict[str, int] = field(default_factory=dict)
    loaded_deferred_tools: list[str] = field(default_factory=list)

    @property
    def last(self) -> Decision | None:
        return self.decisions[-1] if self.decisions else None

    @property
    def has_decision(self) -> bool:
        return bool(self.decisions)

    def tool_results(self) -> list[dict[str, Any]]:
        """The current decision's results, in the order its calls were made."""
        decision = self.last
        if decision is None:
            return []
        results = []
        for call in decision.tool_calls:
            step = decision.steps.get(str(call.get("id")))
            if step is None or step.result is None:
                break
            results.append(step.result)
        return results

    def interrupted_step(self) -> tuple[int, ToolStep] | None:
        """The call that was started and never reported back, if there is one."""
        decision = self.last
        if decision is None:
            return None
        for index, call in enumerate(decision.tool_calls):
            step = decision.steps.get(str(call.get("id")))
            if step is None:
                return None
            if step.result is None:
                return (index, step) if step.started is not None else None
        return None

    def display_blocks(self) -> list[dict[str, Any]]:
        """Every text, thinking and tool card this turn has produced.

        Each decision is one provider response, and its first card is marked
        ``round_start`` as the live turn marks it (``turn_runtime.loop_runner``).
        A resumed turn is stored with these cards, and the next turn renders
        its earlier responses from the marks the way they were sent
        (``context/render.py``), so the cached prefix holds.
        """
        blocks: list[dict[str, Any]] = []
        for decision in self.decisions:
            first = len(blocks)
            for item in decision.message.get("content") or ():
                if not isinstance(item, Mapping):
                    continue
                kind = item.get("type")
                if kind == "text" and isinstance(item.get("text"), str) and item["text"]:
                    blocks.append(_signed({"type": "text", "text": item["text"]}, item.get("text_signature")))
                elif kind == "thinking" and (item.get("thinking") or item.get("thinking_signature")):
                    # A reasoning item with no summary is still sent back
                    # (its signature), as the live turn's card keeps it.
                    block = _signed({"type": "thinking", "text": str(item.get("thinking") or "")},
                                    item.get("thinking_signature"))
                    if item.get("redacted"):
                        block["redacted"] = True
                    blocks.append(block)
                elif kind == "toolCall":
                    block = {
                        "type": "tool", "tool": item.get("name"), "tool_call_id": item.get("id"),
                        "input": json.dumps(item.get("arguments") or {}, default=str),
                    }
                    step = decision.steps.get(str(item.get("id")))
                    if step is not None and step.result is not None:
                        block.update(result=_result_text(step.result), is_error=step.is_error,
                                     outcome=step.outcome)
                    blocks.append(block)
            if len(blocks) > first:
                blocks[first]["round_start"] = True
        return blocks


def _signed(block: dict[str, Any], signature: Any) -> dict[str, Any]:
    """``block`` with the provider's signature, as ``turn_runtime.loop_runner`` stores it."""
    if isinstance(signature, str) and signature:
        block["signature"] = signature
    return block


def _result_text(result: Mapping[str, Any]) -> str:
    return "\n".join(
        str(item.get("text") or "") for item in (result.get("content") or ())
        if isinstance(item, Mapping) and item.get("type") == "text"
    )


def apply_journal_record(state: JournalState, record: Mapping[str, Any]) -> None:
    data = record.get("data") or {}
    kind = record.get("type")
    state.position = max(state.position, int(data.get("position") or 0))
    if kind == SNAPSHOT:
        state.snapshot = dict(data.get("snapshot") or {})
    elif kind == DECISION:
        message = dict(data.get("message") or {})
        calls = [item for item in message.get("content") or ()
                 if isinstance(item, Mapping) and item.get("type") == "toolCall"]
        state.decisions.append(Decision(str(data.get("action_id") or ""), message, [dict(c) for c in calls]))
        state.turn = dict(data.get("turn") or state.turn)
        state.loaded_deferred_tools = list(data.get("loaded_deferred_tools") or ())
    elif kind in {TOOL_STARTED, TOOL_RESULT, TOOL_SUSPENDED} and state.decisions:
        tool_call_id = str(data.get("tool_call_id") or "")
        step = state.decisions[-1].steps.setdefault(tool_call_id, ToolStep(tool_call_id))
        if kind == TOOL_STARTED:
            step.tool_name = str(data.get("tool_name") or "")
            step.started = dict(data)
        elif kind == TOOL_SUSPENDED:
            step.started = None
        else:
            step.result = dict(data.get("result") or {})
            step.is_error = bool(data.get("is_error"))
            step.outcome = data.get("outcome")
            state.repeat_failures = dict(data.get("repeat_failures") or {})
            state.loaded_deferred_tools = list(data.get("loaded_deferred_tools") or ())
    elif kind == STEER:
        target = state.decisions[-1].steers_after if state.decisions else state.steers_before
        if not any(item.get("command_id") == data.get("command_id") for item in target):
            target.append(dict(data))


def _records_for(session_id: str, pointer: Mapping[str, Any], sessions) -> list[dict]:
    source = pointer.get("source")
    records = _records_for(session_id, source, sessions) if isinstance(source, Mapping) else []
    position = int(pointer.get("position") or 0)
    own = [record for record in sessions.read_turn_records(session_id, str(pointer["execution_id"]))
           if int((record.get("data") or {}).get("position") or 0) <= position]
    own.sort(key=lambda record: int(record["data"]["position"]))
    return records + own


def load_journal(session_id: str, pointer: Mapping[str, Any], *, sessions=None) -> JournalState:
    """Fold the records a pointer names, following a branch back to its source."""
    state = JournalState(execution_id=str(pointer["execution_id"]))
    for record in _records_for(session_id, pointer, sessions if sessions is not None else _sessions()):
        apply_journal_record(state, record)
    state.position = int(pointer.get("position") or state.position)
    return state


def latest_pointer(session_id: str, execution_id: str, base: Mapping[str, Any] | None = None,
                   *, sessions=None) -> dict[str, Any] | None:
    """A pointer to all the execution recorded, on top of a published ``base``."""
    store = sessions if sessions is not None else _sessions()
    records = store.read_turn_records(session_id, execution_id)
    if not records:
        return dict(base) if base else None
    pointer: dict[str, Any] = {
        "execution_id": execution_id,
        "position": max(int((record.get("data") or {}).get("position") or 0) for record in records),
    }
    if base:
        source = base.get("source") if base.get("execution_id") == execution_id else base
        if source:
            pointer["source"] = dict(source)
    return pointer


def manifest_fields(state: JournalState, pointer: Mapping[str, Any], *,
                    waiting_on: str | None = None,
                    restart_window_seconds: int = 0) -> tuple[str, tuple[dict, ...], dict[str, Any]]:
    """(safe point kind, frontier, state refs) naming where this turn stands."""
    decision = state.last
    if decision is None:
        raise ValueError("a turn with no model decision has no position to publish")
    next_tool_index = len(state.tool_results())
    phase = "after_provider" if waiting_on or not next_tool_index else "after_tool"
    kind = ("agent.wait.before_tool" if waiting_on else
            "agent.provider.decision.after" if phase == "after_provider" else "agent.tool.action.after")
    step_id = f"wait:{waiting_on}" if waiting_on else f"{phase}:{decision.action_id}:{next_tool_index}"
    frontier = ({"step_id": step_id, "phase": phase, "branch_id": "main"},)
    return kind, frontier, {
        "agent_journal": dict(pointer),
        "restart_window_seconds": restart_window_seconds,
        "agent_position": {
            "safe_point": {"kind": kind, "step_id": step_id, "phase": phase},
            "frontier": [dict(item) for item in frontier],
            "turn": dict(state.turn),
            "current_decision": {
                "provider_action_id": decision.action_id,
                "tool_call_ids": [str(call.get("id")) for call in decision.tool_calls],
            },
            "next_tool_index": next_tool_index,
        },
    }


def interrupted_outcome(step: ToolStep) -> str:
    """What to do about a tool that started and never reported back.

    ``rerun`` when running it again is harmless or when nothing shows it
    changed anything; otherwise ``report`` and let the model look.
    """
    if step.tool_name in _READ_ONLY_TOOLS:
        return "rerun"
    started = step.started or {}
    if step.tool_name in _FILE_EDIT_TOOLS and isinstance(started.get("path"), str):
        if _file_digest(started["path"]) == started.get("file_digest"):
            return "rerun"
    return "report"
