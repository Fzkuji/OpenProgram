"""List files a single turn modified (task H).

Wire format::

    in:  {"action": "list_turn_files", "session_id": "...", "assistant_msg_id": "..."}
    out: {"type": "list_turn_files_result",
          "data": {"session_id", "assistant_msg_id", "paths": [...], "error"?}}

Reads ``BackupStore.list_backed_paths(turn_id)``; ``turn_id`` is the
assistant message id (same key the file backup store uses on write).
"""
from __future__ import annotations

import asyncio
import json


def _list_paths(session_id: str, assistant_msg_id: str) -> dict:
    from openprogram.store.session.session_store import default_store
    from openprogram.store.snapshot.file_backup import BackupStore

    store = default_store()
    pair = store._open(session_id)
    if pair is None:
        return {"paths": [], "error": f"unknown session {session_id!r}"}
    git, _idx = pair
    backup = BackupStore(git.path)
    try:
        return {"paths": list(backup.list_backed_paths(assistant_msg_id))}
    except Exception as e:  # noqa: BLE001
        return {"paths": [], "error": f"{type(e).__name__}: {e}"}


async def handle_list_turn_files(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    assistant_msg_id = (cmd.get("assistant_msg_id") or "").strip()

    if not session_id or not assistant_msg_id:
        payload = {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "paths": [],
            "error": "session_id and assistant_msg_id are required",
        }
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: _list_paths(session_id, assistant_msg_id),
        )
        payload = {
            "session_id": session_id,
            "assistant_msg_id": assistant_msg_id,
            "paths": result.get("paths") or [],
        }
        if result.get("error"):
            payload["error"] = result["error"]

    await ws.send_text(json.dumps({
        "type": "list_turn_files_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "list_turn_files": handle_list_turn_files,
}
