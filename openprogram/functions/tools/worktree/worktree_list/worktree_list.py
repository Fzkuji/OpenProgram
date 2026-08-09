"""worktree_list — list active / merged / discarded worktree entries."""
from __future__ import annotations

from typing import Optional

from openprogram.functions._runtime import function
from openprogram.functions.tools.worktree.shared import _current_session_id
from openprogram.worktree.manager import get_manager
from openprogram.worktree.types import WorktreeStatus


@function(
    name="worktree_list",
    description=(
        "List worktrees, newest first. By default returns every "
        "worktree the agent has touched (all statuses). Filter with "
        "status_filter to focus on running work.\n"
        "\n"
        "Args:\n"
        "  status_filter: optional comma-separated subset of "
        "active / committing / merged / discarded / kept / errored. "
        "Empty = no filter.\n"
        "  scope: 'session' (default; only worktrees bound to this "
        "session) or 'all' (every worktree in the profile)."
    ),
    toolset=["core"],
)
def worktree_list(status_filter: str = "", scope: str = "session") -> str:
    """List worktrees with optional status filter."""
    mgr = get_manager()
    sid = _current_session_id()
    filt: Optional[set[WorktreeStatus]] = None
    if status_filter and status_filter.strip():
        names = [s.strip() for s in status_filter.split(",") if s.strip()]
        out: set[WorktreeStatus] = set()
        for n in names:
            try:
                out.add(WorktreeStatus(n))
            except ValueError:
                return (
                    f"[worktree_list error] unknown status {n!r}; "
                    "use one of: active / committing / merged / "
                    "discarded / kept / errored."
                )
        filt = out

    rows = mgr.list_worktrees(
        status_filter=filt,
        parent_session=sid if scope.strip() == "session" else None,
    )
    if not rows:
        return "[worktree_list] no worktrees"
    lines = [
        f"{wt.id}  {wt.status.value:10s}  {wt.branch_name}  "
        f"({wt.source_repo} → {wt.worktree_path})"
        for wt in rows
    ]
    return "[worktree_list]\n" + "\n".join(lines)
