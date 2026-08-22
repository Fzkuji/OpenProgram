"""Resolve owned child change sets for file-history operations."""
from __future__ import annotations

from typing import Any


def owned_change_set_closure(
    session_id: str,
    origin_turn_ids: list[str],
) -> dict[str, Any]:
    from openprogram.agent.job.store import list_jobs
    from openprogram.agent.job.types import JobStatus, is_terminal

    origins = set(origin_turn_ids)
    included: list[str] = []
    blockers: list[dict] = []
    linked: list[dict] = []
    jobs = list_jobs(session_id)
    changed = True
    while changed:
        changed = False
        for job in jobs:
            origin = job.origin_turn_id or job.caller_msg_id or job.parent_msg_id
            if not origin or origin not in origins:
                continue
            # Ownership is an authority grant for destructive history
            # operations. Legacy jobs without the explicit origin field are
            # ambiguous and must never inherit the old `creates_agent=True`
            # default as permission to restore their files.
            relation = (
                job.relation
                if job.origin_turn_id
                else "worktree" if job.worktree_id
                else "linked"
            )
            impact = {
                "job_id": job.id,
                "relation": relation,
                "origin_turn_id": origin,
                "head_id": job.head_id,
                "status": job.status.value,
                "worktree_id": job.worktree_id,
            }
            if relation != "owned" or job.worktree_id:
                if impact not in linked:
                    linked.append(impact)
                if relation == "linked" and not job.worktree_id \
                        and not is_terminal(job.status) and impact not in blockers:
                    blockers.append(impact)
                continue
            if not is_terminal(job.status):
                if impact not in blockers:
                    blockers.append(impact)
                continue
            if job.status != JobStatus.COMPLETED or not job.head_id:
                continue
            if job.head_id not in origins:
                origins.add(job.head_id)
                included.append(job.head_id)
                changed = True
    try:
        from openprogram.store.session.session_store import default_store

        pair = default_store()._open(session_id)
        index = pair[1] if pair else None
        included.sort(
            key=lambda turn_id: (
                index.nodes_by_id[turn_id].seq
                if index and turn_id in index.nodes_by_id
                else -1
            ),
            reverse=True,
        )
    except Exception:
        # Unknown legacy nodes stay included but keep their discovery order;
        # preflight will reject a discontinuous journal before any write.
        pass
    return {
        "status": "blocked" if blockers else "ready",
        "owned_turn_ids": included,
        "blockers": blockers,
        "linked": linked,
    }
