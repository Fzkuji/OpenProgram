"""Agent-facing worktree tools.

Five ``@function`` LLM-callable tools wrap
:class:`openprogram.worktree.manager.WorktreeManager`:

  * ``worktree_create`` — ``git worktree add`` on a source repo
  * ``worktree_merge``  — merge worktree branch back into source repo
  * ``worktree_discard``— ``git worktree remove --force`` + branch -D
  * ``worktree_list``   — list active / merged / discarded entries
  * ``worktree_keep``   — detach: keep the worktree dir + branch but
                          stop OpenProgram from binding to it

Each tool returns a human-readable string so the LLM can act on the
result without parsing structured JSON. Errors surface as
``[worktree_<op> error] <code>: <detail>``.

Side effect of ``worktree_create``: the new worktree becomes the
**session's active worktree** — the next agent turn picks it up
through ``current_worktree_path()``. Only one active worktree per
session at a time (Part 5 #4).
"""
from __future__ import annotations

import os
import time
from typing import Optional

from openprogram.functions._runtime import function
from openprogram.worktree.context import set_worktree, clear_worktree
from openprogram.worktree.manager import WorktreeError, get_manager
from openprogram.worktree.types import WorktreeStatus


def _current_session_id() -> Optional[str]:
    """Pull the session id from the dispatcher's ContextVar — same
    source the task tool uses."""
    try:
        from openprogram.webui._pause_stop import _current_session_id as _sid
        return _sid.get(None)
    except Exception:
        return None


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
        "repo.\n"
        "  base_ref: starting ref (default HEAD). Use 'origin/main' "
        "etc. to pin against a specific upstream.\n"
        "  label: short slug for the worktree directory name (1-3 "
        "words). Helps when listing multiple worktrees in a session."
    ),
    toolset=["core"],
    requires_approval=True,
)
def worktree_create(
    source_repo: str = "",
    branch_name: str = "",
    base_ref: str = "HEAD",
    label: str = "",
) -> str:
    """Create a worktree and bind it to the current session.

    Args:
        source_repo: Absolute path of the source git repo.
        branch_name: Branch to create on the worktree. Defaults to
            ``op/wt/<slug>-<id6>``.
        base_ref: Starting ref. Defaults to ``HEAD``.
        label: Short slug used in the worktree directory name.
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

    # If this was the session's active worktree, clear the ContextVar
    # so subsequent tool calls go back to the source repo / default cwd.
    if sid:
        active = mgr.find_active_for_session(sid)
        if active is None:
            clear_worktree()

    summary = (
        f"[worktree_merge] id={wt.id} merged_into={wt.source_repo} "
        f"strategy={strategy} files_changed={wt.files_changed} "
        f"merge_sha={wt.merge_sha or 'n/a'}"
    )
    return summary


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

    if sid:
        active = mgr.find_active_for_session(sid)
        if active is None:
            clear_worktree()

    return f"[worktree_discard] id={wt.id} status={wt.status.value}"


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

    if sid:
        active = mgr.find_active_for_session(sid)
        if active is None:
            clear_worktree()

    return (
        f"[worktree_keep] id={wt.id} status=kept "
        f"path={wt.worktree_path} branch={wt.branch_name}"
    )


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


__all__ = [
    "worktree_create",
    "worktree_merge",
    "worktree_discard",
    "worktree_list",
    "worktree_keep",
]
