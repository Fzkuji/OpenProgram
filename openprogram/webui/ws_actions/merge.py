"""Merge-branches WS action — aggregate N peer sessions into one reply.

Wire format::

    in:  {"action": "merge_branches",
          "session_id": "...",                  // target session
          "sub_sessions": ["sid_a", "sid_b"],   // peer sessions to merge
          "message": "...",                      // user merge instruction
          "agent_id": "main"}
    out: {"type": "merge_branches_result",
          "data": {"session_id", "target_assistant_id", "commit_id",
                   "parent_ids", "final_text", "failed", "error"?}}

The kept field name ``merge_branches`` is historical (the very first
prototype committed sub-agents onto git branches in the parent repo).
The behavior is now session-level: no branches, no worktrees, just
peer-session ids. Clients written against the action name keep
working; only the body field renamed from ``sub_branches`` to
``sub_sessions``.
"""
from __future__ import annotations

import asyncio
import json


def _run(
    target_session_id: str,
    sub_sessions: list[str],
    message: str,
    agent_id: str,
) -> dict:
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        target_session_id=target_session_id,
        sub_sessions=sub_sessions,
        message=message,
        agent_id=agent_id,
    )
    return {
        "target_assistant_id": out.target_assistant_id,
        "commit_id": out.commit_id,
        "parent_ids": list(out.parent_ids),
        "final_text": out.final_text,
        "failed": out.failed,
        "error": out.error,
    }


async def handle_merge_branches(ws, cmd: dict) -> None:
    target_session_id = (cmd.get("session_id") or "").strip()
    sub_sessions = cmd.get("sub_sessions") or []
    if not isinstance(sub_sessions, list):
        sub_sessions = []
    sub_sessions = [s for s in (str(b).strip() for b in sub_sessions) if s]
    message = cmd.get("message") or ""
    agent_id = (cmd.get("agent_id") or "main").strip() or "main"

    if not target_session_id or not sub_sessions:
        payload = {
            "session_id": target_session_id,
            "target_assistant_id": None,
            "commit_id": None,
            "parent_ids": [],
            "final_text": "",
            "failed": True,
            "error": "session_id and sub_sessions are required",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _run(target_session_id, sub_sessions, message, agent_id),
        )
        payload = {"session_id": target_session_id, **result}

    await ws.send_text(json.dumps({
        "type": "merge_branches_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "merge_branches": handle_merge_branches,
}
