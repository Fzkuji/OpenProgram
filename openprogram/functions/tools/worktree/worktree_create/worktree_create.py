"""worktree_create — ``git worktree add`` on a source repo.

Side effect: the new worktree becomes the **session's active
worktree** — the next agent turn picks it up through
``current_worktree_path()``. Only one active worktree per session at a
time (Part 5 #4).
"""
from __future__ import annotations

import os
from typing import Optional

from openprogram.functions._runtime import function
from openprogram.functions.tools.worktree.shared import _current_session_id
from openprogram.worktree.context import set_worktree
from openprogram.worktree.manager import WorktreeError, get_manager


def _resolve_source_repo(source_repo: str) -> Optional[str]:
    """Source-repo fallback chain (D5):

      1. Explicit ``source_repo`` parameter wins.
      2. ``OPENPROGRAM_WORKDIR`` env / config (the fn-form
         ``Working in a folder``).
      3. ``git rev-parse --show-toplevel`` from the worker's cwd.

    Returns the absolute path of a candidate git repo (caller is
    responsible for verifying it's a real git repo via the manager).
    Returns None if all three fail.
    """
    if source_repo and source_repo.strip():
        return os.path.abspath(os.path.expanduser(source_repo.strip()))
    try:
        from openprogram.paths import get_default_workdir
        wd = get_default_workdir()
        if wd:
            return os.path.abspath(os.path.expanduser(wd))
    except Exception:
        pass
    try:
        import subprocess
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


@function(
    name="worktree_create",
    description=(
        "Create an isolated `git worktree` on a real git repository "
        "(the user's source code, NOT OpenProgram's own session "
        "storage). Subsequent bash / edit / write / read calls in "
        "this session default their cwd to the new worktree, so the "
        "agent can experiment freely without touching the source "
        "repo's working tree. Use worktree_merge to ship the changes "
        "back, worktree_discard to drop them.\n"
        "\n"
        "Args:\n"
        "  source_repo: absolute path of the source git repo. If "
        "omitted, falls back to the session's default workdir, then "
        "to git rev-parse --show-toplevel from the worker cwd.\n"
        "  branch_name: branch to create the worktree on. Defaults "
        "to op/wt/<slug>-<id6>. Must not already exist in the source "
        "repo. Ignored when pr is set.\n"
        "  base_ref: starting ref (default HEAD). Use 'origin/main' "
        "etc. to pin against a specific upstream. Ignored when pr is "
        "set.\n"
        "  label: short slug for the worktree directory name (1-3 "
        "words). Helps when listing multiple worktrees in a session.\n"
        "  pr: open the worktree on a pull request's branch instead of "
        "base_ref — accepts a PR number (123), '#123', or a GitHub PR "
        "URL. Fetches the PR's head via `gh pr view` (same-repo PR: "
        "direct branch fetch; fork PR: GitHub's pull/<n>/head ref) and "
        "checks it out on branch op/wt/pr-<n>-<id6>. Requires `gh`, "
        "authenticated (`gh auth login`). If a worktree already exists "
        "for this PR, returns its path instead of creating a duplicate."
    ),
    toolset=["core"],
    requires_approval=True,
)
def worktree_create(
    source_repo: str = "",
    branch_name: str = "",
    base_ref: str = "HEAD",
    label: str = "",
    pr: str = "",
) -> str:
    """Create a worktree and bind it to the current session.

    Args:
        source_repo: Absolute path of the source git repo.
        branch_name: Branch to create on the worktree. Defaults to
            ``op/wt/<slug>-<id6>``. Ignored when ``pr`` is set.
        base_ref: Starting ref. Defaults to ``HEAD``. Ignored when
            ``pr`` is set.
        label: Short slug used in the worktree directory name.
        pr: PR number / ``#number`` / GitHub PR URL. Opens the
            worktree on that PR's branch instead of ``base_ref``.
    """
    sid = _current_session_id()
    mgr = get_manager()

    # Enforce one-active-worktree-per-session at the tool layer.
    if sid:
        cur_active = mgr.find_active_for_session(sid)
        if cur_active is not None:
            return (
                f"[worktree_create error] already_active: session has an "
                f"active worktree {cur_active.id} at {cur_active.worktree_path}. "
                f"Run worktree_merge / worktree_discard / worktree_keep "
                f"first."
            )

    repo = _resolve_source_repo(source_repo)
    if not repo:
        return (
            "[worktree_create error] source_repo_not_resolved: pass an "
            "absolute path to a git repo, or set OPENPROGRAM_WORKDIR / "
            "the session's working folder so the fallback can find it."
        )

    try:
        wt = mgr.create_worktree(
            repo,
            branch_name=branch_name.strip() or None,
            base_ref=(base_ref or "HEAD").strip() or "HEAD",
            label=label.strip() or None,
            parent_session=sid,
            pr=pr.strip() or None,
        )
    except WorktreeError as e:
        return f"[worktree_create error] {e}"
    except Exception as e:  # noqa: BLE001
        return f"[worktree_create error] unexpected: {type(e).__name__}: {e}"

    # Bind it as the session's active worktree right away — the next
    # bash / edit will see it via the ContextVar. Dispatcher rebinds
    # at every turn entry, so this set is for the rest of THIS turn;
    # the dispatcher hook keeps it bound on subsequent turns.
    set_worktree(wt.worktree_path)

    return (
        f"[worktree_create] id={wt.id} path={wt.worktree_path} "
        f"branch={wt.branch_name} base={wt.base_ref}\n"
        f"Active for this session. bash / edit / write / read now "
        f"default cwd here."
    )
