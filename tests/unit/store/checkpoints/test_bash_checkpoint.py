"""Bash is outside exact turn attribution and never creates mutations."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from openprogram.agent.agent_loop import _execute_tool_calls
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.providers.types import AssistantMessage, TextContent, ToolCall
from openprogram.providers.utils.event_stream import EventStream
from openprogram.store import SessionNodeWriter, SessionStore, _current_turn_id, _store
from openprogram.store.snapshot.checkpoint import CheckpointStore
from openprogram.worktree.context import reset_worktree, set_worktree


def test_bash_write_is_not_attributed_to_turn(tmp_path: Path, monkeypatch) -> None:
    session_id = "bash-no-attribution"
    turn_id = "assistant-1"
    session_store = SessionStore(root_path=tmp_path / "sessions")
    session_store._open(session_id, create_if_missing=True)
    target = tmp_path / "agent.log"

    async def execute(_call_id, _args, _cancel, _on_update):
        target.write_text("background output\n", encoding="utf-8")
        return AgentToolResult(
            content=[TextContent(text="exit_code=0")], details={}, is_error=False,
        )

    tool = AgentTool(
        name="bash",
        description="test bash boundary",
        parameters={"type": "object", "properties": {}},
        label="bash",
        execute=execute,
    )
    message = AssistantMessage(
        content=[ToolCall(id="call-bash", name="bash", arguments={})],
        api="openai-completions",
        provider="openai",
        model="fake",
        stop_reason="toolUse",
        timestamp=int(time.time() * 1000),
    )

    store_token = _store.set(SessionNodeWriter(session_store, session_id))
    turn_token = _current_turn_id.set(turn_id)
    worktree_token = set_worktree(str(tmp_path))
    try:
        def reject_scan(*_args, **_kwargs):
            raise AssertionError("bash execution must not walk the workspace")

        monkeypatch.setattr("os.scandir", reject_scan)
        asyncio.run(_execute_tool_calls([tool], message, None, EventStream()))
    finally:
        reset_worktree(worktree_token)
        _current_turn_id.reset(turn_token)
        _store.reset(store_token)

    assert target.read_text(encoding="utf-8") == "background output\n"
    assert CheckpointStore(
        session_store._session_dir(session_id),
    ).list_mutations(turn_id) == []
