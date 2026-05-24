"""Merge-branches WS action (task F).

Wire format::

    in:  {"action": "merge_branches",
          "session_id": "...",
          "sub_branches": ["sub_...", "sub_..."],
          "message": "...",
          "agent_id": "main"}
    out: {"type": "merge_branches_result",
          "data": {"session_id", "parent_node_id", "commit_id",
                   "parent_ids", "final_text", "failed", "error"?}}
"""
from __future__ import annotations

import asyncio
import json


def _run(
    session_id: str,
    sub_branches: list[str],
    message: str,
    agent_id: str,
) -> dict:
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        parent_session_id=session_id,
        sub_branches=sub_branches,
        message=message,
        agent_id=agent_id,
    )
    return {
        "parent_node_id": out.parent_node_id,
        "commit_id": out.commit_id,
        "parent_ids": list(out.parent_ids),
        "final_text": out.final_text,
        "failed": out.failed,
        "error": out.error,
    }


async def handle_merge_branches(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    sub_branches = cmd.get("sub_branches") or []
    if not isinstance(sub_branches, list):
        sub_branches = []
    sub_branches = [b for b in (str(b).strip() for b in sub_branches) if b]
    message = cmd.get("message") or ""
    agent_id = (cmd.get("agent_id") or "main").strip() or "main"

    if not session_id or not sub_branches:
        payload = {
            "session_id": session_id,
            "parent_node_id": None,
            "commit_id": None,
            "parent_ids": [],
            "final_text": "",
            "failed": True,
            "error": "session_id and sub_branches are required",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: _run(session_id, sub_branches, message, agent_id),
        )
        payload = {"session_id": session_id, **result}

    await ws.send_text(json.dumps({
        "type": "merge_branches_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "merge_branches": handle_merge_branches,
}
