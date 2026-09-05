"""WebSocket action for a same-session Agent spawn."""
from __future__ import annotations

import asyncio
import json


def _run(
    session_id: str,
    parent_msg_id: str | None,
    prompt: str,
    agent_id: str,
    label: str | None,
    context: str,
) -> dict:
    from openprogram.agent.sub_agent_run import run_agent_turn

    branch_from = parent_msg_id if context == "inherit" else None
    result = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        branch_from=branch_from,
        label=label,
    )
    return {
        "context": context,
        "head_id": result.head_id,
        "final_text": result.final_text,
        "failed": result.failed,
        "error": result.error,
    }


async def handle_spawn_agent(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    parent_msg_id = (cmd.get("parent_msg_id") or "").strip() or None
    prompt = cmd.get("prompt") or ""
    agent_id = (cmd.get("agent_id") or "main").strip() or "main"
    label = cmd.get("label")
    if isinstance(label, str):
        label = label.strip() or None
    context = "clean" if (cmd.get("context") or "inherit").strip().lower() == "clean" else "inherit"

    if not session_id or not prompt:
        payload = {
            "session_id": session_id,
            "parent_msg_id": parent_msg_id,
            "context": context,
            "head_id": None,
            "final_text": "",
            "failed": True,
            "error": "session_id and prompt are required",
        }
    elif context == "inherit" and not parent_msg_id:
        payload = {
            "session_id": session_id,
            "parent_msg_id": parent_msg_id,
            "context": context,
            "head_id": None,
            "final_text": "",
            "failed": True,
            "error": "parent_msg_id is required when context='inherit'",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _run(
                session_id, parent_msg_id, prompt, agent_id, label, context,
            ),
        )
        payload = {
            "session_id": session_id,
            "parent_msg_id": parent_msg_id,
            **result,
        }

    await ws.send_text(json.dumps({
        "type": "spawn_agent_result",
        "data": payload,
    }, default=str))


ACTIONS = {"spawn_agent": handle_spawn_agent}
