"""WorktreeManager — the process-wide owner of git worktree lifecycle.

Implements ``docs/design/runtime/agent-worktree.md`` Part 4 step 2:

  * :meth:`create_worktree` — ``git worktree add`` + persist
  * :meth:`merge_worktree` — ``git merge`` (ff-only / squash / no-ff)
  * :meth:`discard_worktree` — ``git worktree remove --force`` + branch -D
  * :meth:`get_worktree` / :meth:`list_worktrees`

Safety: every create call validates that the prospective
``worktree_path`` is NOT under ``~/.openprogram/sessions/`` (D4) and
the ``source_repo`` IS a real git repo (D14). Merge failures keep
the worktree alive (Part 5 #3); the entity transitions ``committing``
→ ``active`` with an ``error`` stamp.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from openprogram.paths import get_state_dir
from openprogram.worktree.store import (
    delete_worktree as _store_delete,
    find_active_for_session,
    list_worktrees as _store_list,
    load_worktree,
    save_worktree,
)
from openprogram.worktree.types import (
    Worktree,
    WorktreeStatus,
    can_transition,
    is_terminal,
    mint_worktree_id,
)


def _broadcast_worktree_status(wt: Worktree) -> None:
    """Push a ``worktree_status`` envelope through the WS server.

    Mirrors :func:`openprogram.agent.task.runner._broadcast_task_status`
    — best-effort, silently swallows the case where webui isn't
    imported (CLI / tests / library use). The full row is included
    inline so the right-rail panel can patch its local cache without
    a second round-trip.
    """
    try:
        from openprogram.webui import server as _s
    except ImportError:
        # webui is optional (CLI / tests / library use). Skip silently
        # — this is the documented expected path when no UI is running.
        return
    try:
        _s._broadcast(json.dumps({
            "type": "worktree_status",
            "data": {
                "worktree_id": wt.id,
                "status": wt.status.value,
                "parent_session": wt.parent_session,
                "parent_task": wt.parent_task,
                "branch_name": wt.branch_name,
                "source_repo": wt.source_repo,
                "merge_sha": wt.merge_sha,
                "error": wt.error,
                "worktree": wt.to_dict(),
            },
        }, default=str))
    except Exception as e:
        # Broadcast itself failed despite webui being importable —
        # connection corruption, serialization error, etc. The state
        # transition has already been persisted, so we don't raise,
        # but log to stderr so the issue isn't completely silent.
        import sys
        print(
            f"[worktree-broadcast] {type(e).__name__}: {e}",
            file=sys.stderr,
        )


# Subprocess timeout cap. Git operations on user repos should be fast;
# 60s is a generous ceiling that still avoids deadlock if a hook hangs.
_GIT_TIMEOUT_SECS = 60.0


class WorktreeError(Exception):
    """Wrapper for git failures so callers can distinguish them from
    Python-side errors. The string is suitable for surfacing to the
    LLM (it includes git's stderr)."""


def _run_git(*args: str, cwd: str) -> tuple[int, str, str]:
    """Run ``git`` with the given args. Returns ``(returncode, stdout,
    stderr)``. Never raises on normal exits (non-zero is OK and
    surfaced via the return tuple)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECS,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as e:
        raise WorktreeError(f"git binary not found: {e}") from e
    except subprocess.TimeoutExpired:
        return -1, "", f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECS}s"


def _is_git_repo(path: str) -> bool:
    """True iff ``path`` is the working tree (or .git dir) of a real
    git repo. Empty paths / nonexistent paths short-circuit False."""
    if not path:
        return False
    p = Path(path).expanduser()
    if not p.exists():
        return False
    rc, _, _ = _run_git("rev-parse", "--git-dir", cwd=str(p))
    return rc == 0


def _slugify(name: str, default: str) -> str:
    """Sanitize a label for use in a git ref or directory name."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip()).strip("-")
    return (cleaned[:32] or default).lower()


def _sessions_git_root() -> Path:
    """Directory that OpenProgram uses for its own session-as-git
    storage. Worktrees MUST NOT be created inside this tree.

    Mirrors ``session_store._default_root`` — the ``sessions-git`` →
    ``sessions`` rename applies here too. We don't trigger the rename
    (session_store owns that); we just point at the new canonical name,
    falling back to the legacy dir if only it exists.
    """
    state = get_state_dir()
    new = state / "sessions"
    old = state / "sessions-git"
    if new.exists() or not old.exists():
        return new
    return old


def _is_inside_sessions_dir(path: Path) -> bool:
    sessions_root = _sessions_git_root().resolve()
    try:
        path = path.resolve()
    except Exception:
        path = path.absolute()
    try:
        path.relative_to(sessions_root)
        return True
    except ValueError:
        return False


def _worktrees_root() -> Path:
    return get_state_dir() / "worktrees"


class WorktreeManager:
    """Process-wide singleton. Use :func:`get_manager`.

    Thread-safe via ``self._lock``. State transitions go through
    :meth:`_transition`, which validates against
    :func:`worktree.types.can_transition` and persists the row.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────

    def create_worktree(
        self,
        source_repo: str,
        *,
        branch_name: Optional[str] = None,
        base_ref: str = "HEAD",
        label: Optional[str] = None,
        parent_session: Optional[str] = None,
        parent_task: Optional[str] = None,
    ) -> Worktree:
        """Create a new worktree on ``source_repo``.

        Raises :class:`WorktreeError` on:

          * source_repo is not a git repo (``not_a_git_repo``)
          * source_repo is inside OpenProgram's session-git tree
            (``worktree_in_sessions_dir``)
          * a worktree with the same branch_name already exists
            (``worktree_exists``)
          * the underlying ``git worktree add`` fails (passes stderr)

        Caller (tool wrapper) is responsible for higher-level checks
        like "session already has an active worktree" — the manager
        only enforces the per-repo / filesystem invariants.
        """
        source_repo = os.path.abspath(os.path.expanduser(source_repo))
        if not _is_git_repo(source_repo):
            raise WorktreeError(
                f"not_a_git_repo: {source_repo!r} is not a git repo"
            )
        if _is_inside_sessions_dir(Path(source_repo)):
            raise WorktreeError(
                "worktree_in_sessions_dir: source_repo must not be under "
                f"{_sessions_git_root()}; that subtree belongs to "
                "OpenProgram's session storage."
            )

        # Reject ref names that could be misparsed as git CLI options.
        # ``git worktree add -b <branch> [<commit-ish>]`` and the later
        # ``merge`` / ``branch -D`` calls all pass these names directly
        # to git as positional args; a leading ``-`` would let an
        # attacker-controlled label inject options (``--upload-pack=...``
        # etc.). The other rejections — empty / whitespace / NUL — are
        # the same constraints git itself applies but with a clearer
        # error message before we shell out.
        def _validate_ref(name: str, kind: str) -> None:
            if not name or "\x00" in name or name.startswith("-"):
                raise WorktreeError(
                    f"invalid_{kind}: {name!r} is not a valid git ref name "
                    f"(must not be empty, start with '-', or contain NUL)."
                )

        wt_id = mint_worktree_id()
        slug = _slugify(label or "wt", default=wt_id.replace("wt_", ""))
        branch = (branch_name or "").strip() or f"op/wt/{slug}-{wt_id[3:9]}"
        _validate_ref(branch, "branch_name")
        _validate_ref(base_ref, "base_ref")
        # Final worktree path lives outside both source_repo and
        # sessions-git so neither can accidentally pick up its files.
        path = _worktrees_root() / f"{wt_id}-{slug}"
        if _is_inside_sessions_dir(path):
            raise WorktreeError(
                "worktree_in_sessions_dir: worktree path would land in "
                f"{_sessions_git_root()}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise WorktreeError(
                f"worktree_exists: path {path} already exists; "
                "pass a different label or remove the directory first."
            )

        # Reject duplicate branch refs on the source_repo — git would
        # also reject, but we surface a clean error string.
        rc, _, _ = _run_git(
            "rev-parse", "--verify", f"refs/heads/{branch}",
            cwd=source_repo,
        )
        if rc == 0:
            raise WorktreeError(
                f"worktree_exists: branch {branch!r} already exists in "
                f"{source_repo}; pass a unique branch_name."
            )

        rc, out, err = _run_git(
            "worktree", "add", str(path), "-b", branch, base_ref,
            cwd=source_repo,
        )
        if rc != 0:
            raise WorktreeError(
                f"git worktree add failed (rc={rc}): {err.strip() or out.strip()}"
            )

        wt = Worktree(
            id=wt_id,
            source_repo=source_repo,
            worktree_path=str(path),
            branch_name=branch,
            base_ref=base_ref,
            status=WorktreeStatus.ACTIVE,
            parent_session=parent_session,
            parent_task=parent_task,
        )
        save_worktree(wt)
        # First-time appearance — `_transition` is only called on
        # subsequent state changes, so we have to push the initial
        # row explicitly. Right-rail panel relies on this to surface
        # newly-created worktrees without polling.
        _broadcast_worktree_status(wt)
        return wt

    def get_worktree(self, worktree_id: str) -> Optional[Worktree]:
        return load_worktree(worktree_id)

    def list_worktrees(
        self,
        *,
        status_filter: Optional[set[WorktreeStatus]] = None,
        parent_session: Optional[str] = None,
        parent_task: Optional[str] = None,
    ) -> list[Worktree]:
        return _store_list(
            status_filter=status_filter,
            parent_session=parent_session,
            parent_task=parent_task,
        )

    def find_active_for_session(self, session_id: str) -> Optional[Worktree]:
        return find_active_for_session(session_id)

    def merge_worktree(
        self,
        worktree_id: str,
        *,
        strategy: str = "ff-only",
        delete_branch: bool = False,
    ) -> Worktree:
        """Merge the worktree's branch back into ``source_repo`` HEAD.

        Strategies:

          * ``ff-only`` (default) — fails when not fast-forward.
          * ``squash`` — ``git merge --squash`` + auto-commit with a
            generic message (the agent can amend with bash later).
          * ``no-ff`` — always create a merge commit.

        On failure the worktree status returns to ``active`` with an
        ``error`` field populated. On success it moves to ``merged``,
        the worktree directory is removed, and (optionally) the branch
        is deleted.
        """
        wt = load_worktree(worktree_id)
        if wt is None:
            raise WorktreeError(f"unknown worktree {worktree_id!r}")
        if wt.status != WorktreeStatus.ACTIVE:
            raise WorktreeError(
                f"worktree {worktree_id} is in status {wt.status.value}; "
                "merge only allowed from active."
            )
        # Refuse if there are uncommitted / untracked changes — agent
        # has to commit explicitly first (D7). ``git status --porcelain``
        # is empty when clean.
        rc, out, _ = _run_git("status", "--porcelain", cwd=wt.worktree_path)
        if rc == 0 and out.strip():
            raise WorktreeError(
                "worktree_dirty: uncommitted or untracked changes in "
                f"{wt.worktree_path}; commit or stash first.\n"
                f"--- git status --porcelain ---\n{out.rstrip()}"
            )

        # Move to committing — gates concurrent merge attempts.
        self._transition(wt, WorktreeStatus.COMMITTING)

        try:
            args: tuple[str, ...]
            if strategy == "ff-only":
                args = ("merge", "--ff-only", wt.branch_name)
            elif strategy == "squash":
                args = ("merge", "--squash", wt.branch_name)
            elif strategy == "no-ff":
                args = ("merge", "--no-ff", wt.branch_name, "-m",
                        f"merge worktree {wt.id} ({wt.branch_name})")
            else:
                raise WorktreeError(
                    f"unknown merge strategy {strategy!r}; "
                    "expected ff-only / squash / no-ff."
                )
            rc, out, err = _run_git(*args, cwd=wt.source_repo)
            if strategy == "squash" and rc == 0:
                # --squash leaves index staged; commit explicitly.
                rc2, out2, err2 = _run_git(
                    "commit", "-m",
                    f"squash-merge worktree {wt.id} ({wt.branch_name})",
                    cwd=wt.source_repo,
                )
                if rc2 != 0:
                    rc, out, err = rc2, out2, err2
            if rc != 0:
                # Surface a typed error so the tool wrapper can map to
                # the design's error codes.
                msg = err.strip() or out.strip() or f"git merge failed rc={rc}"
                code = "merge_conflict" if "conflict" in msg.lower() else (
                    "not_fast_forward" if "not possible" in msg.lower()
                    or "non-fast-forward" in msg.lower() else "merge_failed"
                )
                # Move back to active so the caller can retry / discard.
                wt.error = f"{code}: {msg}"
                self._transition(wt, WorktreeStatus.ACTIVE)
                raise WorktreeError(wt.error)

            # Capture the merge sha + diff size for audit.
            rc_head, head_out, _ = _run_git(
                "rev-parse", "HEAD", cwd=wt.source_repo,
            )
            merge_sha = head_out.strip() if rc_head == 0 else None
            # Files changed: count unique paths between base_ref and HEAD.
            rc_diff, diff_out, _ = _run_git(
                "diff", "--name-only", f"{wt.base_ref}..HEAD",
                cwd=wt.source_repo,
            )
            files_changed = (
                len([ln for ln in diff_out.splitlines() if ln.strip()])
                if rc_diff == 0 else 0
            )

            # Remove the worktree directory itself; safe to do here
            # since we already verified clean above.
            rc_rm, _, err_rm = _run_git(
                "worktree", "remove", wt.worktree_path,
                cwd=wt.source_repo,
            )
            if rc_rm != 0:
                # Force-remove rather than leaving a half-state.
                _run_git(
                    "worktree", "remove", "--force", wt.worktree_path,
                    cwd=wt.source_repo,
                )

            if delete_branch:
                _run_git(
                    "branch", "-D", wt.branch_name, cwd=wt.source_repo,
                )

            wt.merge_sha = merge_sha
            wt.files_changed = files_changed
            wt.error = None
            self._transition(wt, WorktreeStatus.MERGED)
            return wt
        except WorktreeError:
            raise
        except Exception as e:  # noqa: BLE001
            wt.error = f"unexpected_failure: {type(e).__name__}: {e}"
            self._transition(wt, WorktreeStatus.ACTIVE)
            raise WorktreeError(wt.error) from e

    def discard_worktree(
        self,
        worktree_id: str,
        *,
        force: bool = False,
        delete_branch: bool = True,
    ) -> Worktree:
        """Tear down the worktree without merging.

        ``force=False`` (default) refuses to discard a dirty worktree;
        the caller has to set ``force=True`` to drop uncommitted work.
        """
        wt = load_worktree(worktree_id)
        if wt is None:
            raise WorktreeError(f"unknown worktree {worktree_id!r}")
        if is_terminal(wt.status):
            # Idempotent — already discarded / merged / kept. Just
            # return the current row.
            return wt

        if not force:
            rc, out, _ = _run_git(
                "status", "--porcelain", cwd=wt.worktree_path,
            )
            if rc == 0 and out.strip():
                raise WorktreeError(
                    "worktree_dirty: uncommitted changes in "
                    f"{wt.worktree_path}; pass force=True to drop them."
                )

        # Run remove. force=True maps to --force on git side too.
        rm_args: tuple[str, ...] = ("worktree", "remove", wt.worktree_path)
        if force:
            rm_args = ("worktree", "remove", "--force", wt.worktree_path)
        rc, _, err = _run_git(*rm_args, cwd=wt.source_repo)
        if rc != 0:
            # If the worktree directory was already gone (user nuked
            # it manually) git's ``prune`` cleans up — re-run.
            _run_git("worktree", "prune", cwd=wt.source_repo)

        if delete_branch:
            _run_git(
                "branch", "-D", wt.branch_name, cwd=wt.source_repo,
            )

        wt.error = None
        self._transition(wt, WorktreeStatus.DISCARDED)
        return wt

    def keep_worktree(self, worktree_id: str) -> Worktree:
        """User decides to detach the worktree from OpenProgram but
        keep the directory + branch. The status flips to ``kept`` —
        we no longer set ContextVar to it, but the on-disk worktree
        is intact for the user to ``cd`` into.
        """
        wt = load_worktree(worktree_id)
        if wt is None:
            raise WorktreeError(f"unknown worktree {worktree_id!r}")
        if is_terminal(wt.status):
            return wt
        self._transition(wt, WorktreeStatus.KEPT)
        return wt

    # ── Internals ───────────────────────────────────────────────

    def _transition(
        self,
        wt: Worktree,
        new_status: WorktreeStatus,
    ) -> Worktree:
        """Validate + apply a state change, then persist."""
        with self._lock:
            changed = wt.status != new_status
            if changed:
                if not can_transition(wt.status, new_status):
                    raise WorktreeError(
                        f"illegal worktree transition {wt.status.value} → "
                        f"{new_status.value} (worktree {wt.id})"
                    )
                wt.status = new_status
                if is_terminal(new_status) and wt.completed_at is None:
                    wt.completed_at = time.time()
            save_worktree(wt)
        # Broadcast outside the lock — the WS server has its own
        # synchronisation, and we don't want a slow socket fan-out
        # holding the manager-wide lock. Also broadcast on no-op
        # saves (e.g. error-stamp without status flip) so any panel
        # reading the row gets the updated `error` field.
        _broadcast_worktree_status(wt)
        return wt


# ── Module-level singleton ─────────────────────────────────────

_manager_lock = threading.Lock()
_manager: Optional[WorktreeManager] = None


def get_manager() -> WorktreeManager:
    """Process-wide WorktreeManager."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = WorktreeManager()
        return _manager


def _reset_manager_for_tests() -> None:
    """Test helper — drop the singleton so each test sees a fresh manager."""
    global _manager
    with _manager_lock:
        _manager = None


__all__ = [
    "WorktreeError",
    "WorktreeManager",
    "get_manager",
    "_reset_manager_for_tests",
]
