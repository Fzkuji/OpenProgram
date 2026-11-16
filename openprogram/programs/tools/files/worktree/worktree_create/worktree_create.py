"""worktree_create — ``git worktree add`` on a source repo.

Side effect: the new worktree becomes the **session's active
worktree** — the next agent turn picks it up through
``current_worktree_path()``. Only one active worktree per session at a
time (Part 5 #4).
"""
from __future__ import annotations

import os
from typing import Optional

from openprogram.programs._host.runtime import function
from openprogram.programs.tools.files.worktree.shared import _current_session_id
from openprogram.state.worktree.context import set_worktree
from openprogram.state.worktree.manager import WorktreeError, get_manager


def _resolve_source_repo(source_repo: str, session_id: Optional[str] = None) -> Optional[str]:
    """Source-repo fallback chain (D5):

      1. Explicit ``source_repo`` parameter wins.
      2. The conversation's project folder.
      3. The configured default folder (``OPENPROGRAM_WORKDIR`` env /
         ``default_workdir`` config).

    Never the worker's own working directory: that is wherever the
    worker started (a checkout of OpenProgram, under a test run), not
    a folder the person chose. Returns the absolute path of a candidate
    git repo (the manager verifies it), or None.
    """
    if source_repo and source_repo.strip():
        return os.path.abspath(os.path.expanduser(source_repo.strip()))
    from openprogram.runtime.conversation.session_settings.workdir import project_workdir_for
    from openprogram.state.paths import configured_workdir
    folder = project_workdir_for(session_id) if session_id else None
    wd = str(folder) if folder is not None else configured_workdir()
    if wd:
        return os.path.abspath(os.path.expanduser(wd))
    return None


@function(
    name="worktree_create",
    description=(
        "Create an isolated `git worktree` on a real git repository "
        "(the user's source code, NOT OpenProgram's own session "
        "storage). Subsequent bash / edit / write / read / glob / grep "
        "/ list / apply_patch calls on this conversation branch resolve "
        "relative paths and their default folder against the new "
        "worktree; other branches keep the checkout. Use worktree_merge "
        "to ship the changes back, worktree_discard to drop them.\n"
        "\n"
        "Args:\n"
        "  source_repo: absolute path of the source git repo. If "
        "omitted, uses the conversation's project folder, then the "
        "configured default folder.\n"
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
    # Creating a worktree changes nothing the user has; only discarding
    # one (a deletion) asks first.
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
    from openprogram.state.worktree.store import current_branch_for
    branch = current_branch_for(sid) if sid else None

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

    repo = _resolve_source_repo(source_repo, sid)
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
            parent_branch=branch,
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

    msg = (
        f"[worktree_create] id={wt.id} path={wt.worktree_path} "
        f"branch={wt.branch_name} base={wt.base_ref}\n"
        f"Active for this branch. File tools and bash now resolve "
        f"relative paths here."
    )
    if wt.include_synced:
        msg += f"\n.worktreeinclude: copied {len(wt.include_synced)} file(s): " \
               f"{', '.join(wt.include_synced)}"
    if wt.include_failed:
        msg += f"\n.worktreeinclude: {len(wt.include_failed)} file(s) failed: " \
               f"{'; '.join(wt.include_failed)}"
    return msg
