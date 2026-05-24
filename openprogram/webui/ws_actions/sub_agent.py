"""Spawn-sub-agent WS action — peer-session model.

Wire format::

    in:  {"action": "spawn_sub_agent",
          "session_id": "...",                 // parent attaching to
          "parent_msg_id": "...",              // assistant turn it hangs off
          "prompt": "...",                      // sub-agent's instruction
          "agent_id": "main",
          "label": "..."  (optional)}
    out: {"type": "spawn_sub_agent_result",
          "data": {"session_id", "parent_msg_id",
                   "sub_session_id", "sub_head_id", "sub_commit_id",
                   "attach_node_id",
                   "final_text", "failed", "error"?}}

The sub-agent runs as an independent peer session. The parent session
receives a single attach pointer node in its DAG; clients open the
sub-session by its id (same way they'd open any other session) when
the user expands the attach card.
"""
from __future__ import annotations

import asyncio
import json


def _run(
    session_id: str,
    parent_msg_id: str,
    prompt: str,
    agent_id: str,
    label: str | None,
) -> dict:
    from openprogram.agent.sub_agent_run import run_sub_agent_turn
    result = run_sub_agent_turn(
        parent_session_id=session_id,
        parent_assistant_id=parent_msg_id,
        prompt=prompt,
        agent_id=agent_id,
        label=label,
    )
    return {
        "sub_session_id": result.sub_session_id,
        "sub_head_id": result.sub_head_id,
        "sub_commit_id": result.sub_commit_id,
        "attach_node_id": result.attach_node_id,
        "final_text": result.final_text,
        "failed": result.failed,
        "error": result.error,
    }


async def handle_spawn_sub_agent(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    parent_msg_id = (cmd.get("parent_msg_id") or "").strip()
    prompt = cmd.get("prompt") or ""
    agent_id = (cmd.get("agent_id") or "main").strip() or "main"
    label = cmd.get("label")
    if isinstance(label, str):
        label = label.strip() or None

    if not session_id or not parent_msg_id or not prompt:
        payload = {
            "session_id": session_id,
            "parent_msg_id": parent_msg_id,
            "sub_session_id": "",
            "final_text": "",
            "failed": True,
            "error": "session_id, parent_msg_id and prompt are required",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _run(session_id, parent_msg_id, prompt, agent_id, label),
        )
        payload = {
            "session_id": session_id,
            "parent_msg_id": parent_msg_id,
            **result,
        }

    await ws.send_text(json.dumps({
        "type": "spawn_sub_agent_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "spawn_sub_agent": handle_spawn_sub_agent,
}
