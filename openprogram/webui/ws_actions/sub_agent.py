"""Spawn-sub-agent WS action (task E part 3).

Wire format::

    in:  {"action": "spawn_sub_agent",
          "session_id": "...",
          "parent_msg_id": "...",
          "prompt": "...",
          "agent_id": "...",
          "label": "..." (optional)}
    out: {"type": "spawn_sub_agent_result",
          "data": {"session_id", "parent_msg_id", "branch", "final_text",
                   "failed", "error"?, "parent_node_id", "sub_commit_sha"}}

The handler offloads ``run_sub_agent_turn`` to a thread so the WS event
loop isn't blocked while the sub-agent runs its full LLM turn
(potentially many seconds). Cancellation isn't wired in for v1 —
the sub-agent runs to completion regardless of WS disconnect.
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
        "branch": result.branch,
        "final_text": result.final_text,
        "failed": result.failed,
        "error": result.error,
        "parent_node_id": result.parent_node_id,
        "sub_commit_sha": result.sub_commit_sha,
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
            "branch": "",
            "final_text": "",
            "failed": True,
            "error": "session_id, parent_msg_id and prompt are required",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: _run(session_id, parent_msg_id, prompt, agent_id, label),
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
