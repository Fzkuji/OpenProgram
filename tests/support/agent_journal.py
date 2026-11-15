"""Build Agent turn journals without a session store."""
from __future__ import annotations

from types import SimpleNamespace


class MemorySessions:
    """The two session-store calls a journal makes, held in a list."""

    def __init__(self):
        self.records: list[dict] = []

    def append_turn_record(self, session_id, kind, data):
        self.records.append({"ts": len(self.records), "type": kind, "data": dict(data)})

    def read_turn_records(self, session_id, execution_id):
        return [item for item in self.records if item["data"]["execution_id"] == execution_id]


def journal_continuation(request, *, decision, snapshot, tool_results=(), earlier=(),
                         execution_id="exec-1", sessions=None, assistant_message_id=None):
    """A continuation resumed after ``decision`` and its finished ``tool_results``.

    ``earlier`` lists (decision, results) pairs that came before it.
    """
    from openprogram.runtime.conversation.checkpoint import AgentContinuation
    from openprogram.runtime.execution.agent_journal import AgentJournal

    sessions = sessions if sessions is not None else MemorySessions()
    journal = AgentJournal(request.session_id, execution_id, sessions=sessions)
    journal.record_snapshot(snapshot)
    turn = {
        "user_message_id": request.user_msg_id,
        "assistant_message_id": assistant_message_id or f"{request.user_msg_id}_reply",
        "base_history_head_id": request.user_msg_id,
    }
    for index, (message, results) in enumerate([*earlier, (decision, tool_results)]):
        journal.record_decision(message.model_dump(mode="json"), action_id=f"action-{index}", turn=turn)
        for result in results:
            journal.record_tool_started(result.tool_call_id, result.tool_name, {})
            journal.record_tool_result(result.tool_call_id, result.model_dump(mode="json"),
                                       is_error=result.is_error, outcome="completed", repeat_failures={})
    checkpoint = SimpleNamespace(execution_id=execution_id, state_refs={"agent_journal": journal.pointer()})
    return AgentContinuation.from_checkpoint(store=None, checkpoint=checkpoint, request=request, sessions=sessions)
