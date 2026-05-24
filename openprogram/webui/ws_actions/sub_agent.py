"""Spawn-agent WS action — same-session multi-agent.

Wire format::

    in:  {"action": "spawn_agent",
          "session_id": "...",                 // session
          "parent_msg_id": "..." | null,       // node to fork off, null = new root
          "prompt": "...",
          "agent_id": "main",
          "context": "inherit" | "clean",      // default: inherit
          "label": "..."}
    out: {"type": "spawn_agent_result",
          "data": {"session_id", "parent_msg_id", "context",
                   "head_id", "final_text", "failed", "error"?}}

``context="inherit"``: the spawned agent forks off ``parent_msg_id``
and inherits the chain that led to it. ``head_id`` is the new
branch tip in the same session.

``context="clean"``: the spawned agent starts at a new root inside
the same session (``parent_id=null``). It sees only the prompt.
Result lands as an independent DAG tree alongside the original
conversation.

Legacy action name ``spawn_sub_agent`` (and the ``mode`` /
``detached`` parameter) remain mapped here so older clients keep
working — they all route into the same ``run_agent_turn`` now.
"""
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
    pid = parent_msg_id if context == "inherit" else None
    result = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        parent_id=pid,
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
    # Accept both the new ``context`` field and the legacy ``mode``
    # field. Legacy "detached" maps to "clean", "inline" to "inherit".
    raw = (cmd.get("context") or cmd.get("mode") or "inherit").strip().lower()
    if raw in ("detached", "clean"):
        context = "clean"
    else:
        context = "inherit"

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


ACTIONS = {
    "spawn_agent": handle_spawn_agent,
    # Legacy alias — old clients still send these. Treat identically.
    "spawn_sub_agent": handle_spawn_agent,
}
