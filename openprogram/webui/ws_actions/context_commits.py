"""Context-commit timeline WS actions: list_commits / get_commit_detail.

Exposes the per-session context-commit chain to the right-dock
``Context`` tab so users can inspect what the LLM actually saw on
each turn.

For each commit we also return ``turn_group_id`` so the UI can
collapse multiple parallel-branch commits (same fork point in the
DAG → multi-agent / modify-retry attempts) into a single row with
an attempt switcher. See ``_find_fork_group`` for the rule.
"""
from __future__ import annotations

import json


def _find_fork_group(idx, head_id: str) -> str:
    """Group key for a commit, based on its head's DAG ancestry.

    Walk up the conv-predecessor chain from ``head_id``. The first
    ancestor that has >1 conv child is the fork point — its id is
    the group key, so every commit whose head descends through that
    fork shares the same row in the Context panel (with an attempt
    switcher).

    If no fork is found (linear chain), the commit gets its own
    single-attempt group keyed by its head id.
    """
    visited: set[str] = set()
    cur: str | None = head_id
    while cur and cur not in visited:
        visited.add(cur)
        node = idx.nodes_by_id.get(cur)
        if node is None:
            break
        parent = (node.metadata or {}).get("parent_id")
        if not parent:
            break
        siblings = idx.children_by_predecessor.get(parent, [])
        if len(siblings) > 1:
            return parent
        cur = parent
    return head_id  # no fork → single-attempt group keyed by self


async def handle_list_commits(ws, cmd: dict):
    session_id = cmd.get("session_id")
    commits: list[dict] = []
    err: str | None = None
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.context.commit import (
            list_commits,
            commit_state_counts,
        )
        store = default_db()
        if session_id:
            entries = list_commits(store, session_id, limit=50)
            # Open the session's in-memory index so we can compute
            # turn_group_id per commit. Best-effort — falls back to
            # head_id-self group if the index isn't available.
            pair = store._open(session_id)
            idx = pair[1] if pair else None
            for s in entries:
                group_id = (
                    _find_fork_group(idx, s.head_node_id)
                    if idx else s.head_node_id
                )
                commits.append({
                    "id": s.id,
                    "parent_id": s.parent_id,
                    "created_at": s.created_at,
                    "head_node_id": s.head_node_id,
                    "turn_group_id": group_id,
                    "total_tokens": s.total_tokens,
                    "rules_version": s.rules_version,
                    "summary": s.summary,
                    "item_count": len(s.items),
                    "state_counts": commit_state_counts(s),
                })
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "context_commits_list",
        "data": {
            "session_id": session_id,
            "commits": commits,
            "error": err,
        },
    }, default=str))


async def handle_get_commit_detail(ws, cmd: dict):
    commit_id = cmd.get("commit_id")
    payload: dict = {"id": commit_id, "error": None, "items": []}
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.context.commit import load_commit
        store = default_db()
        if not commit_id:
            payload["error"] = "commit_id required"
        else:
            commit = load_commit(store, commit_id)
            if commit is None:
                payload["error"] = f"commit {commit_id!r} not found"
            else:
                payload.update({
                    "id": commit.id,
                    "session_id": commit.session_id,
                    "parent_id": commit.parent_id,
                    "created_at": commit.created_at,
                    "head_node_id": commit.head_node_id,
                    "rules_version": commit.rules_version,
                    "total_tokens": commit.total_tokens,
                    "summary": commit.summary,
                    "items": [
                        {
                            "source_node_id": i.source_node_id,
                            "role": i.role,
                            "state": i.state,
                            "rendered": i.rendered,
                            "tokens": i.tokens,
                            "reason": i.reason,
                            "locked": i.locked,
                            "is_anchor": i.is_anchor,
                            "merged_into": i.merged_into,
                        }
                        for i in commit.items
                    ],
                })
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    await ws.send_text(json.dumps({
        "type": "context_commit_detail",
        "data": payload,
    }, default=str))


ACTIONS = {
    "list_context_commits": handle_list_commits,
    "get_context_commit_detail": handle_get_commit_detail,
}
