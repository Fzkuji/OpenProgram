"""Sub-agent dispatcher integration (task E part 3).

Allocates a worktree, runs a turn against it, posts the result back
into the parent session's DAG as a tool_result-style node.

Design — three constraints make this work:

1. **Worktree isolation.** ``allocate_sub_agent(...)`` (see
   ``sub_agent.py``) materializes ``<parent_repo>/_worktrees/<branch>/``
   bound to a fresh branch. File edits + history/context writes inside
   that dir affect only the sub-branch.

2. **SessionStore rooted at worktrees.** We spin up a thin
   ``SessionStore(<parent_repo>/_worktrees)`` and use the branch name
   as that store's session_id. ``_open`` resolves to a ``GitSession``
   pointing at the worktree path. ``_ensure_init`` skips ``git init``
   when ``.git`` already exists (the worktree's ``.git`` is a file
   pointing into the parent's ``.git/worktrees/<branch>/``). All
   subsequent writes — history JSON, context commits, meta — land
   inside the worktree dir and get committed onto the sub-branch.

3. **ContextVar override.** Dispatcher reads ``default_db()`` at the
   top of ``process_user_turn``. We set a ContextVar-scoped override
   so the sub-agent's dispatcher invocation transparently routes
   through the sub-store while the parent's singleton is untouched.

After completion, the parent's session DAG gets a tool_result row
``role="tool", function="sub_agent"`` pointing at the sub-branch so
the UI (and a future merge turn) can find the work.

The sub-agent's branch is left intact on release so merge turns can
still walk the commits later.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SubAgentTurnResult:
    """Outcome of a sub-agent turn. ``error`` is populated when the
    allocation or dispatch path failed before the LLM was reached;
    ``failed`` mirrors ``TurnResult.failed`` when the LLM ran but
    errored. The parent DAG node id (``parent_node_id``) is the id of
    the tool_result row written into the parent's session, useful for
    the UI / a follow-up merge."""
    branch: str = ""
    worktree_path: Optional[Path] = None
    final_text: str = ""
    failed: bool = False
    error: Optional[str] = None
    parent_node_id: Optional[str] = None
    sub_commit_sha: Optional[str] = None


def run_sub_agent_turn(
    parent_session_id: str,
    parent_assistant_id: str,
    prompt: str,
    agent_id: str,
    *,
    label: Optional[str] = None,
    base_ref: str = "HEAD",
) -> SubAgentTurnResult:
    """Run a one-shot sub-agent turn isolated to a worktree.

    Parent's session is responsible for telling the UI "spawning sub-
    agent ..." before this returns; this function is synchronous and
    blocks until the sub-agent finishes (caller should run it in a
    thread when triggered from the WS event loop).
    """
    from openprogram.agent.sub_agent import allocate_sub_agent, release_sub_agent
    from openprogram.agent.session_db import (
        default_db, set_db_override, reset_db_override,
    )
    from openprogram.store.session_store import SessionStore

    parent_store = default_db()
    parent_pair = parent_store._open(parent_session_id)
    if parent_pair is None:
        return SubAgentTurnResult(
            error=f"parent session {parent_session_id!r} not found",
        )
    parent_git, _ = parent_pair
    parent_git._ensure_init()

    ws = allocate_sub_agent(
        parent_session_id, parent_assistant_id,
        label=label, base_ref=base_ref,
    )
    if ws is None:
        return SubAgentTurnResult(
            error=f"failed to allocate worktree for {parent_session_id!r}",
        )

    sub_store = SessionStore(parent_git.path / "_worktrees")
    sub_sid = ws.branch

    # Pre-create the sub-store's session row so the dispatcher doesn't
    # call create_session inside the LLM loop (which would race against
    # the worktree's .git that was set up via `git worktree add`).
    sub_store.create_session(
        sub_sid, agent_id,
        title=label or ws.branch,
        source="sub_agent",
    )

    result_text = ""
    result_failed = False
    result_error: Optional[str] = None
    sub_commit_sha: Optional[str] = None

    token = set_db_override(sub_store)
    try:
        from openprogram.agent.dispatcher import (
            TurnRequest, process_user_turn,
        )
        req = TurnRequest(
            session_id=sub_sid,
            user_text=prompt,
            agent_id=agent_id,
            source="sub_agent",
        )
        try:
            turn_result = process_user_turn(req)
            result_text = turn_result.final_text or ""
            result_failed = bool(turn_result.failed)
            result_error = turn_result.error
        except Exception as e:  # noqa: BLE001
            # Dispatcher raised before producing a TurnResult — record
            # the failure but still proceed to release the worktree.
            result_failed = True
            result_error = f"{type(e).__name__}: {e}"

        # Whatever the dispatcher wrote into the worktree's history/ +
        # context/ now needs a commit on the sub-branch. commit_turn
        # is a no-op when nothing's staged (e.g. dispatcher errored
        # before any write), so this is safe in both paths.
        try:
            sub_commit_sha = sub_store.commit_turn(
                sub_sid,
                f"sub_agent turn ({label or ws.branch})",
            )
        except Exception as e:  # noqa: BLE001
            # Commit failure shouldn't tank the whole call — the parent
            # DAG node still gets written so the user sees the result.
            if result_error is None:
                result_error = f"sub_commit failed: {type(e).__name__}: {e}"
    finally:
        reset_db_override(token)
        # Worktree dir comes down so it's not littering parent's
        # _worktrees/; the branch ref + commits survive so a future
        # merge turn (task F) can walk them.
        try:
            release_sub_agent(ws)
        except Exception:
            pass

    # Post the summary node into the parent's DAG so the UI sees one
    # row "ran sub_agent" pointing at the sub-branch.
    parent_node_id = uuid.uuid4().hex[:12]
    try:
        parent_store.append_message(parent_session_id, {
            "id": parent_node_id,
            "role": "tool",
            "function": "sub_agent",
            "content": result_text or (result_error or "(no output)"),
            "parent_id": parent_assistant_id,
            "timestamp": time.time(),
            "is_error": bool(result_failed or result_error),
            "extra": json.dumps({
                "tool_use": {
                    "name": "sub_agent",
                    "arguments": json.dumps({
                        "label": label or "",
                        "branch": ws.branch,
                        "prompt": (prompt or "")[:500],
                    }, default=str),
                    "called_by": parent_assistant_id,
                },
                "sub_agent": {
                    "branch": ws.branch,
                    "commit_sha": sub_commit_sha,
                    "label": label or "",
                },
            }, default=str),
        })
        parent_store.commit_turn(
            parent_session_id,
            f"sub_agent summary ({label or ws.branch})",
        )
    except Exception as e:  # noqa: BLE001
        if result_error is None:
            result_error = f"parent DAG write failed: {type(e).__name__}: {e}"

    return SubAgentTurnResult(
        branch=ws.branch,
        worktree_path=ws.path,
        final_text=result_text,
        failed=result_failed,
        error=result_error,
        parent_node_id=parent_node_id,
        sub_commit_sha=sub_commit_sha,
    )
