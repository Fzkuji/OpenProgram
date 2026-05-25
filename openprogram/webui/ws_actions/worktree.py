"""Worktree WS actions — list / get / merge / discard / keep.

Wire shape (all messages JSON envelopes)::

  list:
    in   {"action": "list_worktrees", "session_id": "..." | null,
          "status_filter": ["active", ...]?, "scope": "session"|"all"}
    out  {"type": "worktrees_list",
          "data": {"session_id"?, "worktrees": [<dict>, ...]}}

  get:
    in   {"action": "get_worktree", "worktree_id": "..."}
    out  {"type": "worktree",
          "data": {"worktree": <dict>|null}}

  merge:
    in   {"action": "merge_worktree", "worktree_id": "...",
          "strategy": "ff-only"|"squash"|"no-ff",
          "delete_branch": false}
    out  {"type": "merge_worktree_result",
          "data": {"worktree_id", "status", "merge_sha"?, "error"?}}

  discard:
    in   {"action": "discard_worktree", "worktree_id": "...",
          "force": false, "delete_branch": true}
    out  {"type": "discard_worktree_result",
          "data": {"worktree_id", "status", "error"?}}

  keep:
    in   {"action": "keep_worktree", "worktree_id": "..."}
    out  {"type": "keep_worktree_result",
          "data": {"worktree_id", "status", "error"?}}

These actions are user-initiated (right-rail Worktree panel). The
agent-facing tools (worktree_create / worktree_merge / ...) are
separate; both go through the same :class:`WorktreeManager`.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


def _serialise(wt) -> dict[str, Any]:
    return wt.to_dict()


async def handle_list_worktrees(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip() or None
    scope = (cmd.get("scope") or "session").strip().lower() or "session"
    sf = cmd.get("status_filter") or None

    from openprogram.worktree.manager import get_manager
    from openprogram.worktree.types import WorktreeStatus
    status_filter = None
    if isinstance(sf, list) and sf:
        try:
            status_filter = {WorktreeStatus(s) for s in sf}
        except ValueError:
            status_filter = None
    mgr = get_manager()

    def _read():
        return mgr.list_worktrees(
            status_filter=status_filter,
            parent_session=session_id if scope == "session" else None,
        )

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _read)
    await ws.send_text(json.dumps({
        "type": "worktrees_list",
        "data": {
            "session_id": session_id,
            "worktrees": [_serialise(w) for w in rows],
        },
    }, default=str))


async def handle_get_worktree(ws, cmd: dict) -> None:
    worktree_id = (cmd.get("worktree_id") or "").strip()
    if not worktree_id:
        await ws.send_text(json.dumps({
            "type": "worktree",
            "data": {"worktree": None, "error": "worktree_id is required"},
        }, default=str))
        return
    from openprogram.worktree.manager import get_manager
    mgr = get_manager()

    def _read():
        return mgr.get_worktree(worktree_id)

    loop = asyncio.get_event_loop()
    wt = await loop.run_in_executor(None, _read)
    await ws.send_text(json.dumps({
        "type": "worktree",
        "data": {"worktree": _serialise(wt) if wt else None},
    }, default=str))


async def handle_merge_worktree(ws, cmd: dict) -> None:
    worktree_id = (cmd.get("worktree_id") or "").strip()
    strategy = (cmd.get("strategy") or "ff-only").strip() or "ff-only"
    delete_branch = bool(cmd.get("delete_branch") or False)
    if not worktree_id:
        await ws.send_text(json.dumps({
            "type": "merge_worktree_result",
            "data": {"worktree_id": None, "status": None,
                     "error": "worktree_id is required"},
        }, default=str))
        return
    from openprogram.worktree.manager import get_manager, WorktreeError
    mgr = get_manager()

    def _merge():
        try:
            wt = mgr.merge_worktree(
                worktree_id, strategy=strategy,
                delete_branch=delete_branch,
            )
            return {"worktree": wt, "error": None}
        except WorktreeError as e:
            cur = mgr.get_worktree(worktree_id)
            return {"worktree": cur, "error": str(e)}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _merge)
    wt = result.get("worktree")
    await ws.send_text(json.dumps({
        "type": "merge_worktree_result",
        "data": {
            "worktree_id": worktree_id,
            "status": (wt.status.value if wt else None),
            "merge_sha": (getattr(wt, "merge_sha", None) if wt else None),
            "error": result.get("error"),
        },
    }, default=str))


async def handle_discard_worktree(ws, cmd: dict) -> None:
    worktree_id = (cmd.get("worktree_id") or "").strip()
    force = bool(cmd.get("force") or False)
    delete_branch = (
        bool(cmd.get("delete_branch"))
        if "delete_branch" in cmd else True
    )
    if not worktree_id:
        await ws.send_text(json.dumps({
            "type": "discard_worktree_result",
            "data": {"worktree_id": None, "status": None,
                     "error": "worktree_id is required"},
        }, default=str))
        return
    from openprogram.worktree.manager import get_manager, WorktreeError
    mgr = get_manager()

    def _discard():
        try:
            wt = mgr.discard_worktree(
                worktree_id, force=force,
                delete_branch=delete_branch,
            )
            return {"worktree": wt, "error": None}
        except WorktreeError as e:
            cur = mgr.get_worktree(worktree_id)
            return {"worktree": cur, "error": str(e)}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _discard)
    wt = result.get("worktree")
    await ws.send_text(json.dumps({
        "type": "discard_worktree_result",
        "data": {
            "worktree_id": worktree_id,
            "status": (wt.status.value if wt else None),
            "error": result.get("error"),
        },
    }, default=str))


async def handle_keep_worktree(ws, cmd: dict) -> None:
    worktree_id = (cmd.get("worktree_id") or "").strip()
    if not worktree_id:
        await ws.send_text(json.dumps({
            "type": "keep_worktree_result",
            "data": {"worktree_id": None, "status": None,
                     "error": "worktree_id is required"},
        }, default=str))
        return
    from openprogram.worktree.manager import get_manager, WorktreeError
    mgr = get_manager()

    def _keep():
        try:
            wt = mgr.keep_worktree(worktree_id)
            return {"worktree": wt, "error": None}
        except WorktreeError as e:
            cur = mgr.get_worktree(worktree_id)
            return {"worktree": cur, "error": str(e)}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _keep)
    wt = result.get("worktree")
    await ws.send_text(json.dumps({
        "type": "keep_worktree_result",
        "data": {
            "worktree_id": worktree_id,
            "status": (wt.status.value if wt else None),
            "error": result.get("error"),
        },
    }, default=str))


ACTIONS = {
    "list_worktrees": handle_list_worktrees,
    "get_worktree": handle_get_worktree,
    "merge_worktree": handle_merge_worktree,
    "discard_worktree": handle_discard_worktree,
    "keep_worktree": handle_keep_worktree,
}
