"""Folder Git WS actions — the composer's per-folder git pills.

Wire format (every reply echoes ``path`` so concurrent pills each
pick up their own answer)::

    in   {"action": "git_folder_status", "path": "/abs",
          "include_branches"?: bool, "include_pr"?: bool}
    out  {"type": "git_folder_status", "data": {"path", "status"}}

    in   {"action": "git_switch_branch", "path": "/abs",
          "branch": "name", "create"?: bool, "carry"?: bool}
    out  {"type": "git_switch_branch_result",
          "data": {"path", "ok", "status"?, "error"?, "code"?, "files"?, "detail"?}}

``code`` / ``files`` / ``detail`` come from ``FolderGitError`` (see there);
every refusal frame may carry them.

    in   {"action": "git_create_worktree", "path": "/abs", "branch": "name"}
    out  {"type": "git_worktree_created",
          "data": {"path", "ok", "worktree"?: <status>, "error"?}}

    in   {"action": "git_create_pr", "path": "/abs", "base"?: "main"}
    out  {"type": "git_pr_created",
          "data": {"path", "ok", "url"?, "number"?, "existing"?, "error"?}}

The logic lives in :mod:`openprogram.state.worktree.folder_git`; this module
only moves it off the event loop and frames the replies.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


async def _reply(ws, frame_type: str, data: dict[str, Any]) -> None:
    await ws.send_text(json.dumps({"type": frame_type, "data": data}, default=str))


async def handle_git_folder_status(ws, cmd: dict) -> None:
    from openprogram.state.worktree import folder_git

    path = cmd.get("path")
    status = await asyncio.to_thread(
        folder_git.folder_status,
        path,
        include_branches=bool(cmd.get("include_branches")),
        include_pr=bool(cmd.get("include_pr")),
    )
    await _reply(ws, "git_folder_status", {"path": path, "status": status})


async def _mutation(ws, cmd: dict, frame_type: str, call) -> None:
    from openprogram.state.worktree.folder_git import FolderGitError

    path = cmd.get("path")
    try:
        result = await asyncio.to_thread(call)
    except FolderGitError as exc:
        await _reply(ws, frame_type, {"path": path, "ok": False, **exc.as_dict()})
        return
    await _reply(ws, frame_type, {"path": path, "ok": True, **result})


async def handle_git_switch_branch(ws, cmd: dict) -> None:
    from openprogram.state.worktree import folder_git

    await _mutation(ws, cmd, "git_switch_branch_result", lambda: {
        "status": folder_git.switch_branch(
            cmd.get("path"), cmd.get("branch"),
            create=bool(cmd.get("create")), carry=bool(cmd.get("carry")),
        ),
    })


async def handle_git_create_worktree(ws, cmd: dict) -> None:
    from openprogram.state.worktree import folder_git

    await _mutation(ws, cmd, "git_worktree_created", lambda: {
        "worktree": folder_git.create_worktree(cmd.get("path"), cmd.get("branch")),
    })


async def handle_git_create_pr(ws, cmd: dict) -> None:
    from openprogram.state.worktree import folder_git

    await _mutation(ws, cmd, "git_pr_created", lambda: folder_git.create_pull_request(
        cmd.get("path"), base=cmd.get("base"),
    ))


ACTIONS = {
    "git_folder_status": handle_git_folder_status,
    "git_switch_branch": handle_git_switch_branch,
    "git_create_worktree": handle_git_create_worktree,
    "git_create_pr": handle_git_create_pr,
}
