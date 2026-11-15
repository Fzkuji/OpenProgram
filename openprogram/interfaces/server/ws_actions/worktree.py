"""Worktree WS actions — session list, branch create, list / get / merge / discard / keep.

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
          "strategy": "ff-only"|"squash"|"no-ff"|"auto",
          "delete_branch": false, "commit_pending": false}
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

    from openprogram.state.worktree.manager import get_manager
    from openprogram.state.worktree.types import WorktreeStatus
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
    from openprogram.state.worktree.manager import get_manager
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
    # The Merge button merges what the worktree holds, committed or not.
    commit_pending = bool(cmd.get("commit_pending") or False)
    if not worktree_id:
        await ws.send_text(json.dumps({
            "type": "merge_worktree_result",
            "data": {"worktree_id": None, "status": None,
                     "error": "worktree_id is required"},
        }, default=str))
        return
    from openprogram.state.worktree.manager import get_manager, WorktreeError
    mgr = get_manager()

    def _merge():
        try:
            wt = mgr.merge_worktree(
                worktree_id, strategy=strategy,
                delete_branch=delete_branch,
                commit_pending=commit_pending,
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
    from openprogram.state.worktree.manager import get_manager, WorktreeError
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
    from openprogram.state.worktree.manager import get_manager, WorktreeError
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


def _source_repo_for(session_id: str) -> str | None:
    """The git repository a branch of this conversation can get its own
    worktree of: the top level of the conversation's project folder, or
    ``None`` when that folder is not in a repository."""
    import subprocess
    from openprogram.runtime.conversation.workdir import project_workdir_for
    folder = project_workdir_for(session_id)
    if folder is None:
        return None
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(folder),
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    top = proc.stdout.strip() if proc.returncode == 0 else ""
    return top or None


def session_worktrees_payload(session_id: str) -> dict[str, Any]:
    """Every worktree of one conversation, each with what it holds, and
    the repository a branch without one could get one of."""
    from openprogram.state.worktree.manager import get_manager
    from openprogram.state.worktree.types import WorktreeStatus
    mgr = get_manager()
    rows = []
    for wt in mgr.list_worktrees(parent_session=session_id):
        row = wt.to_dict()
        if wt.status in (WorktreeStatus.ACTIVE, WorktreeStatus.ERRORED, WorktreeStatus.KEPT):
            row["summary"] = mgr.summarize(wt)
        rows.append(row)
    return {
        "session_id": session_id,
        "source_repo": _source_repo_for(session_id),
        "worktrees": rows,
    }


async def handle_session_worktrees(ws, cmd: dict) -> None:
    """in  {"action": "session_worktrees", "session_id"}
    out {"type": "session_worktrees", "data": {session_id, source_repo, worktrees}}"""
    session_id = (cmd.get("session_id") or "").strip()
    if not session_id:
        await ws.send_text(json.dumps({
            "type": "session_worktrees",
            "data": {"session_id": None, "source_repo": None, "worktrees": [],
                     "error": "session_id is required"},
        }, default=str))
        return
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, session_worktrees_payload, session_id)
    await ws.send_text(json.dumps({"type": "session_worktrees", "data": data}, default=str))


def create_branch_worktree(session_id: str, branch_id: str | None, label: str = "") -> dict[str, Any]:
    """Give one branch of a conversation its own worktree; its next turn
    runs there. A branch that already has one gets that one back."""
    from openprogram.state.worktree.manager import WorktreeError, get_manager
    mgr = get_manager()
    if branch_id:
        current = mgr.find_active_for_session(session_id, branch_id)
        if current is not None and current.parent_branch == branch_id:
            return {"worktree": current.to_dict(), "error": None}
    repo = _source_repo_for(session_id)
    if not repo:
        return {"worktree": None,
                "error": "not_a_git_repo: this conversation's folder is not in a git repository"}
    try:
        wt = mgr.create_worktree(
            repo, label=label or "branch", parent_session=session_id,
            parent_branch=branch_id or None,
        )
    except WorktreeError as exc:
        return {"worktree": None, "error": str(exc)}
    return {"worktree": wt.to_dict(), "error": None}


async def handle_create_branch_worktree(ws, cmd: dict) -> None:
    """in  {"action": "create_branch_worktree", "session_id", "branch_id", "label"?}
    out {"type": "create_branch_worktree_result",
         "data": {session_id, branch_id, worktree|null, error|null}}"""
    session_id = (cmd.get("session_id") or "").strip()
    branch_id = (cmd.get("branch_id") or "").strip() or None
    label = (cmd.get("label") or "").strip()
    if not session_id:
        result = {"worktree": None, "error": "session_id is required"}
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, create_branch_worktree, session_id, branch_id, label,
        )
    await ws.send_text(json.dumps({
        "type": "create_branch_worktree_result",
        "data": {"session_id": session_id, "branch_id": branch_id, **result},
    }, default=str))


ACTIONS = {
    "session_worktrees": handle_session_worktrees,
    "create_branch_worktree": handle_create_branch_worktree,
    "list_worktrees": handle_list_worktrees,
    "get_worktree": handle_get_worktree,
    "merge_worktree": handle_merge_worktree,
    "discard_worktree": handle_discard_worktree,
    "keep_worktree": handle_keep_worktree,
}
