"""Spawn-sub-agent WS action — supports inline + detached modes.

Wire format::

    in:  {"action": "spawn_sub_agent",
          "session_id": "...",                 // parent
          "parent_msg_id": "...",              // assistant id to fork from
          "prompt": "...",
          "agent_id": "main",
          "mode": "inline" | "detached",       // default: inline
          "label": "..."}
    out: {"type": "spawn_sub_agent_result",
          "data": {"session_id", "parent_msg_id", "mode",
                   "head_id"?, "sub_session_id"?, "sub_commit_id"?,
                   "attach_node_id"?,
                   "final_text", "failed", "error"?}}

``inline``: the sub-agent inherits the parent conversation and writes
a sibling branch in the same session. Result's ``head_id`` is the
new branch tip; the parent has no extra UI node — the new branch
shows up in the Branches panel like any other fork.

``detached``: the sub-agent gets a brand-new peer session. The
parent's DAG gets an attach pointer card; the peer session appears
in the sidebar.
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
    mode: str,
) -> dict:
    if mode == "inline":
        from openprogram.agent.sub_agent_run import run_inline_agent_turn
        result = run_inline_agent_turn(
            parent_session_id=session_id,
            parent_assistant_id=parent_msg_id,
            prompt=prompt,
            agent_id=agent_id,
            label=label,
        )
    else:
        from openprogram.agent.sub_agent_run import run_sub_agent_turn
        result = run_sub_agent_turn(
            parent_session_id=session_id,
            parent_assistant_id=parent_msg_id,
            prompt=prompt,
            agent_id=agent_id,
            label=label,
        )
    return {
        "mode": result.mode,
        "head_id": result.head_id,
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
    mode = (cmd.get("mode") or "inline").strip().lower() or "inline"
    if mode not in ("inline", "detached"):
        mode = "inline"

    if not session_id or not parent_msg_id or not prompt:
        payload = {
            "session_id": session_id,
            "parent_msg_id": parent_msg_id,
            "mode": mode,
            "sub_session_id": "",
            "final_text": "",
            "failed": True,
            "error": "session_id, parent_msg_id and prompt are required",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _run(
                session_id, parent_msg_id, prompt, agent_id, label, mode,
            ),
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
