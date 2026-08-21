"""worktree_keep — detach: keep the worktree dir + branch but stop
OpenProgram from binding to it."""
from __future__ import annotations

from openprogram.programs._runtime import function
from openprogram.programs.tools.files.worktree.shared import (
    _current_session_id,
    clear_binding_if_no_active,
)
from openprogram.worktree.manager import WorktreeError, get_manager


@function(
    name="worktree_keep",
    description=(
        "Detach a worktree from OpenProgram while preserving the "
        "directory + branch. Useful when the user wants to take over "
        "the worktree in their own editor / terminal. After keep, "
        "OpenProgram stops binding cwd to it and the slot is freed "
        "for a new worktree_create.\n"
        "\n"
        "Args:\n"
        "  worktree_id: id from worktree_create / worktree_list."
    ),
    toolset=["core"],
)
def worktree_keep(worktree_id: str) -> str:
    """Detach the worktree (status → kept). Directory + branch stay."""
    sid = _current_session_id()
    mgr = get_manager()
    if not worktree_id or not isinstance(worktree_id, str):
        return "[worktree_keep error] worktree_id required"
    try:
        wt = mgr.keep_worktree(worktree_id.strip())
    except WorktreeError as e:
        return f"[worktree_keep error] {e}"
    except Exception as e:  # noqa: BLE001
        return f"[worktree_keep error] unexpected: {type(e).__name__}: {e}"

    clear_binding_if_no_active(mgr, sid)

    return (
        f"[worktree_keep] id={wt.id} status=kept "
        f"path={wt.worktree_path} branch={wt.branch_name}"
    )
