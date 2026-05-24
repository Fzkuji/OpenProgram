"""Merge-branches WS action — aggregate N peer branches into one reply.

Wire format::

    in:  {"action": "merge_branches",
          "session_id": "...",                  // target session
          // Either / both of these — coalesced server-side:
          "peers": [{session_id, head_id?}, ...],   // (session, head) pairs
          "sub_sessions": ["sid_a", "sid_b"],       // session ids (=HEAD)
          "message": "...",
          "agent_id": "main"}
    out: {"type": "merge_branches_result",
          "data": {"session_id", "target_assistant_id", "commit_id",
                   "parent_ids", "final_text", "failed", "error"?}}

A "branch" is the abstraction: a ``(session_id, head_id)`` pair.
Same-session merges pass two peers with the same ``session_id`` and
different ``head_id``s. Cross-session merges pass peers from
different sessions. Both go through one code path.

``head_id`` omitted ⇒ that session's current HEAD. ``sub_sessions``
is the legacy shorthand for "all-HEADs" and is kept for back-compat.
"""
from __future__ import annotations

import asyncio
import json


def _run(
    target_session_id: str,
    peers: list[dict],
    sub_sessions: list[str],
    message: str,
    agent_id: str,
) -> dict:
    from openprogram.agent._merge import process_merge_turn
    out = process_merge_turn(
        target_session_id=target_session_id,
        sub_sessions=sub_sessions,
        peers=peers,
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


def _normalize_peers(raw) -> list[dict]:
    """Accept ``[{session_id, head_id}]`` AND the shorthand string forms
    ``"sid"`` / ``"sid:head"`` for convenience. Returns a flat list of
    ``{session_id, head_id}`` dicts (head_id may be None)."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            sid = (item.get("session_id") or "").strip()
            if not sid:
                continue
            head_id = item.get("head_id")
            if isinstance(head_id, str):
                head_id = head_id.strip() or None
            else:
                head_id = None
            out.append({"session_id": sid, "head_id": head_id})
        elif isinstance(item, str):
            s = item.strip()
            if not s:
                continue
            if ":" in s:
                sid, head_id = s.split(":", 1)
                sid = sid.strip()
                head_id = head_id.strip() or None
            else:
                sid = s
                head_id = None
            if sid:
                out.append({"session_id": sid, "head_id": head_id})
    return out


async def handle_merge_branches(ws, cmd: dict) -> None:
    target_session_id = (cmd.get("session_id") or "").strip()
    peers = _normalize_peers(cmd.get("peers"))
    sub_sessions = cmd.get("sub_sessions") or []
    if not isinstance(sub_sessions, list):
        sub_sessions = []
    sub_sessions = [s for s in (str(b).strip() for b in sub_sessions) if s]
    message = cmd.get("message") or ""
    agent_id = (cmd.get("agent_id") or "main").strip() or "main"

    if not target_session_id or not (peers or sub_sessions):
        payload = {
            "session_id": target_session_id,
            "target_assistant_id": None,
            "commit_id": None,
            "parent_ids": [],
            "final_text": "",
            "failed": True,
            "error": "session_id and at least one peer required",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _run(
                target_session_id, peers, sub_sessions, message, agent_id,
            ),
        )
        payload = {"session_id": target_session_id, **result}

    await ws.send_text(json.dumps({
        "type": "merge_branches_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "merge_branches": handle_merge_branches,
}
