"""Revert WS action: roll back the file edits one assistant turn made.

Wire format::

    in:  {"action": "revert_turn", "session_id": "...", "assistant_msg_id": "..."}
    out: {"type": "revert_turn_result",
          "data": {"session_id", "assistant_msg_id", "restored_paths", "error"?}}

Chat history and context commits stay untouched — they're append-only
by design. Only the files this turn edited get rolled back.
"""
from __future__ import annotations

import json


async def handle_revert_turn(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    assistant_msg_id = (cmd.get("assistant_msg_id") or "").strip()

    payload: dict
    if not session_id or not assistant_msg_id:
        payload = {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "restored_paths": [],
            "error": "session_id and assistant_msg_id are required",
        }
    else:
        from openprogram.agent._revert import revert_turn
        # revert_turn is synchronous + does disk I/O; offload from the
        # WS event loop so a slow disk doesn't stall the socket.
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: revert_turn(session_id, assistant_msg_id),
        )
        payload = {
            "session_id": result.get("session_id"),
            "assistant_msg_id": result.get("assistant_msg_id"),
            "restored_paths": result.get("restored_paths") or [],
        }
        if result.get("error"):
            payload["error"] = result["error"]

    await ws.send_text(json.dumps({
        "type": "revert_turn_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "revert_turn": handle_revert_turn,
}
