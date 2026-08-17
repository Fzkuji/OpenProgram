"""worktree_discard — ``git worktree remove --force`` + branch -D."""
from __future__ import annotations

from openprogram.programs._runtime import function
from openprogram.programs.functions.vanilla.worktree.shared import (
    _current_session_id,
    clear_binding_if_no_active,
)
from openprogram.worktree.manager import WorktreeError, get_manager


@function(
    name="worktree_discard",
    description=(
        "Throw away a worktree without merging. By default refuses to "
        "drop uncommitted work — pass force=True to discard anyway.\n"
        "\n"
        "Args:\n"
        "  worktree_id: id from worktree_create / worktree_list.\n"
        "  force: drop uncommitted / untracked changes too. Default false.\n"
        "  delete_branch: delete the branch after removing the "
        "worktree dir. Default true — discard semantics is 'I don't "
        "want this work anywhere'."
    ),
    toolset=["core"],
    requires_approval=True,
)
def worktree_discard(
    worktree_id: str,
    force: bool = False,
    delete_branch: bool = True,
) -> str:
    """Discard a worktree."""
    sid = _current_session_id()
    mgr = get_manager()
    if not worktree_id or not isinstance(worktree_id, str):
        return "[worktree_discard error] worktree_id required"
    try:
        wt = mgr.discard_worktree(
            worktree_id.strip(),
            force=bool(force),
            delete_branch=bool(delete_branch),
        )
    except WorktreeError as e:
        return f"[worktree_discard error] {e}"
    except Exception as e:  # noqa: BLE001
        return f"[worktree_discard error] unexpected: {type(e).__name__}: {e}"

    clear_binding_if_no_active(mgr, sid)

    return f"[worktree_discard] id={wt.id} status={wt.status.value}"
