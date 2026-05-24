"""Sub-agent workspace allocator.

A sub-agent is a side branch in the parent session's git repo: it
gets its own worktree directory off ``<repo>/_worktrees/<branch>/``
so file edits don't trample the parent's ``workdir/``. The actual
turn execution (running a dispatcher loop against the worktree and
posting the result back into the parent DAG) is the next chunk; this
module owns just the bookkeeping primitives:

  * mint a fresh branch name from the parent assistant id
  * call SessionStore to materialize the worktree
  * return both so the caller (a future ``run_sub_agent_turn``) can
    set the sub-agent's cwd and isolate writes
  * teardown on demand

Naming convention: ``sub_<short_assistant_id>_<short_uuid>`` keeps
the branch readable in ``git log --all`` while staying unique even
when the same parent message spawns multiple agents.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SubAgentWorkspace:
    """Result handle for an allocated sub-agent workspace.

    The caller threads ``path`` into the dispatcher as cwd, then calls
    ``release`` (or ``SessionStore.release_sub_agent_worktree``) when
    the sub-agent finishes. The branch ref persists across release so
    the commits made on it are still reachable for a later merge turn.
    """
    branch: str
    path: Path
    session_id: str
    parent_assistant_id: str


def allocate_sub_agent(
    session_id: str,
    parent_assistant_id: str,
    *,
    label: Optional[str] = None,
    base_ref: str = "HEAD",
) -> Optional[SubAgentWorkspace]:
    """Pick a unique branch name and materialize the worktree.

    ``label`` is folded into the branch name when present so multiple
    sub-agents off the same parent stay distinguishable in ``git log
    --all`` (e.g. ``sub_<aid>_searchA_<hex>``). The hex tail guarantees
    uniqueness even with identical labels.
    """
    from openprogram.agent.session_db import default_db

    short_parent = (parent_assistant_id or "x")[:10]
    suffix = uuid.uuid4().hex[:8]
    label_part = f"_{label}" if label else ""
    branch = f"sub_{short_parent}{label_part}_{suffix}"

    store = default_db()
    path = store.allocate_sub_agent_worktree(
        session_id, branch, base_ref=base_ref,
    )
    if path is None:
        return None
    return SubAgentWorkspace(
        branch=branch,
        path=path,
        session_id=session_id,
        parent_assistant_id=parent_assistant_id,
    )


def release_sub_agent(ws: SubAgentWorkspace) -> None:
    """Tear down a sub-agent's worktree. The branch (and any commits
    on it) survive so a follow-up merge turn can still consume them."""
    from openprogram.agent.session_db import default_db
    default_db().release_sub_agent_worktree(ws.session_id, ws.path)
