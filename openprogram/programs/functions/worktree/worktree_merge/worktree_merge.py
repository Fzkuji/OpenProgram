"""worktree_merge — merge a worktree's branch back into its source repo."""
from __future__ import annotations

from openprogram.programs._runtime import function
from openprogram.programs.functions.worktree.shared import (
    _current_session_id,
    clear_binding_if_no_active,
)
from openprogram.worktree.manager import WorktreeError, get_manager


@function(
    name="worktree_merge",
    description=(
        "Merge a worktree's branch back into its source repo and "
        "remove the worktree directory. The worktree must be clean "
        "(no uncommitted / untracked changes) — if dirty, use bash "
        "to commit / stash first.\n"
        "\n"
        "Args:\n"
        "  worktree_id: id from worktree_create / worktree_list.\n"
        "  strategy: 'ff-only' (default; fails when not "
        "fast-forward), 'squash' (squash all worktree commits into "
        "one), or 'no-ff' (always create a merge commit).\n"
        "  delete_branch: if true, delete the worktree's branch after "
        "merging. Default false — branch is preserved for later "
        "auditability."
    ),
    toolset=["core"],
    requires_approval=True,
)
def worktree_merge(
    worktree_id: str,
    strategy: str = "ff-only",
    delete_branch: bool = False,
) -> str:
    """Merge a worktree's branch back into source_repo."""
    sid = _current_session_id()
    mgr = get_manager()
    if not worktree_id or not isinstance(worktree_id, str):
        return "[worktree_merge error] worktree_id required"
    try:
        wt = mgr.merge_worktree(
            worktree_id.strip(),
            strategy=(strategy or "ff-only").strip() or "ff-only",
            delete_branch=bool(delete_branch),
        )
    except WorktreeError as e:
        return f"[worktree_merge error] {e}"
    except Exception as e:  # noqa: BLE001
        return f"[worktree_merge error] unexpected: {type(e).__name__}: {e}"

    clear_binding_if_no_active(mgr, sid)

    return (
        f"[worktree_merge] id={wt.id} merged_into={wt.source_repo} "
        f"strategy={strategy} files_changed={wt.files_changed} "
        f"merge_sha={wt.merge_sha or 'n/a'}"
    )
