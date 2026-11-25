"""``list_harnesses`` and ``update_harness``: the outside agents on the machine a conversation runs on.

An action naming a conversation or project of another machine is forwarded
to that machine's Worker (``remote_machines.forward``), so the answer is
always about the computer the conversation's turns run on.
"""
from __future__ import annotations

import asyncio
import json


async def handle_list_harnesses(ws, cmd: dict):
    from openprogram.runtime.harnesses.models import list_harnesses

    harnesses = await asyncio.to_thread(list_harnesses, refresh=bool(cmd.get("refresh")))
    await ws.send_text(json.dumps({"type": "harnesses_list", "data": {
        "action": "list_harnesses",
        "request_id": cmd.get("request_id"), "session_id": cmd.get("session_id"),
        "harnesses": harnesses,
    }}))


async def handle_update_harness(ws, cmd: dict):
    """Update a harness (Codex) on the conversation's machine with its own installer."""
    from openprogram.runtime.harnesses.models import update_harness

    result = await asyncio.to_thread(update_harness, str(cmd.get("harness") or ""))
    await ws.send_text(json.dumps({"type": "harness_updated", "data": {
        "action": "update_harness",
        "request_id": cmd.get("request_id"), "session_id": cmd.get("session_id"),
        **result,
    }}))


ACTIONS = {"list_harnesses": handle_list_harnesses, "update_harness": handle_update_harness}
