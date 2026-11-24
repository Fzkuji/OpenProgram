"""WorktreeManager — the process-wide owner of git worktree lifecycle.

Implements ``docs/design/runtime/agent-worktree.md`` Part 4 step 2:

  * :meth:`create_worktree` — ``git worktree add`` + persist
  * :meth:`merge_worktree` — ``git merge`` (ff-only / squash / no-ff)
  * :meth:`discard_worktree` — ``git worktree remove --force`` + branch -D
  * :meth:`get_worktree` / :meth:`list_worktrees`

Safety: every create call validates that the prospective
``worktree_path`` is NOT under ``~/.openprogram/projects/`` (D4) and
the ``source_repo`` IS a real git repo (D14). Merge failures keep
the worktree alive (Part 5 #3); the entity transitions ``committing``
→ ``active`` with an ``error`` stamp.
"""
from __future__ import annotations

import os
import re
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from openprogram.state import paths as state_paths
from openprogram.state.worktree.include_sync import sync_include_files
from openprogram.state.worktree.pr_ref import PrRefError, fetch_pr_branch, parse_pr_ref
from openprogram.state.worktree.store import (
    find_active_for_session,
    list_worktrees as _store_list,
    load_worktree,
    save_worktree,
)
from openprogram.state.worktree.types import (
    Worktree,
    WorktreeStatus,
    can_transition,
    is_terminal,
    mint_worktree_id,
)


def _broadcast_worktree_status(wt: Worktree) -> None:
    """Push a ``worktree_status`` envelope through the WS server.

    Mirrors :func:`openprogram.runtime.jobs.runner._broadcast`
    — best-effort. The full row is included inline so the right-rail
    panel can patch its local cache without a second round-trip.

    步 4：走总线（ws.frame 事件），不再 import webui；帧 type/data 字段不变。
    """
    from openprogram.state.event_sink import emit_ws_frame
    emit_ws_frame({
        "type": "worktree_status",
        "data": {
            "worktree_id": wt.id,
            "status": wt.status.value,
            "parent_session": wt.parent_session,
            "parent_job": wt.parent_job,
            "branch_name": wt.branch_name,
            "source_repo": wt.source_repo,
            "merge_sha": wt.merge_sha,
            "error": wt.error,
            "worktree": wt.to_dict(),
        },
    })


# Subprocess timeout cap. Git operations on user repos should be fast;
# 60s is a generous ceiling that still avoids deadlock if a hook hangs.
_GIT_TIMEOUT_SECS = 60.0

logger = logging.getLogger(__name__)

# ``(worktree) -> id of the unfinished helper Job working in it, or None``,
# registered by the Jobs engine (``runtime/jobs/execution_hooks.py``): the
# state layer does not read Jobs itself.
_owner_check: Optional[Callable[["Worktree"], Optional[str]]] = None


def set_owner_check(check: Optional[Callable[["Worktree"], Optional[str]]]) -> None:
    """Install who answers whether a worktree's helper is still working."""
    global _owner_check
    _owner_check = check


class WorktreeError(Exception):
    """Wrapper for git failures so callers can distinguish them from
    Python-side errors. The string is suitable for surfacing to the
    LLM (it includes git's stderr)."""


def _run_git(*args: str, cwd: str) -> tuple[int, str, str]:
    """Run ``git`` with the given args. Returns ``(returncode, stdout,
    stderr)``. Never raises on normal exits (non-zero is OK and
    surfaced via the return tuple)."""
    from openprogram.host._platform.process_ownership import no_window_creation_flags
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=no_window_creation_flags(),
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
    """Directory that OpenProgram uses for its own conversation storage,
    ``<state>/projects``. Worktrees MUST NOT be created inside this tree.
    """
    return state_paths.get_state_dir() / "projects"


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
    return state_paths.get_state_dir() / "worktrees"


class WorktreeManager:
    """Process-wide singleton. Use :func:`get_manager`.

    Thread-safe via ``self._lock``. State transitions go through
    :meth:`_transition`, which validates against
    :func:`worktree.types.can_transition` and persists the row.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # Public API

    def find_for_pr(self, pr_number: int) -> Optional[Worktree]:
        """Non-terminal worktree already opened from this PR, if any."""
        for wt in self.list_worktrees():
            if wt.pr_number == pr_number and not is_terminal(wt.status):
                return wt
        return None

    def create_worktree(
        self,
        source_repo: str,
        *,
        branch_name: Optional[str] = None,
        base_ref: str = "HEAD",
        label: Optional[str] = None,
        parent_session: Optional[str] = None,
        parent_job: Optional[str] = None,
        pr: Optional[str] = None,
        destination: Optional[str] = None,
        parent_branch: Optional[str] = None,
        ephemeral: bool = False,
    ) -> Worktree:
        """Create a new worktree on ``source_repo``.

        ``pr`` (optional): a PR number / ``#number`` / GitHub PR URL.
        When set, the PR's head branch is fetched into a local branch
        first (same-repo PR: direct fetch; fork PR: GitHub's
        ``pull/<n>/head`` synthetic ref) and the worktree is checked
        out there — ``branch_name`` / ``base_ref`` are ignored in this
        mode (the fetched branch fills both roles). Requires the
        ``gh`` CLI, authenticated.

        Raises :class:`WorktreeError` on:

          * source_repo is not a git repo (``not_a_git_repo``)
          * source_repo is inside OpenProgram's session-git tree
            (``worktree_in_sessions_dir``)
          * a worktree with the same branch_name already exists
            (``worktree_exists``)
          * a non-terminal worktree already exists for this PR
            (``pr_worktree_exists``)
          * ``gh`` is missing/unauthenticated, or the PR lookup/fetch
            fails (``pr_ref_error``)
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

        pr_number: Optional[int] = None
        if pr is not None and str(pr).strip():
            try:
                pr_number = parse_pr_ref(str(pr))
            except PrRefError as e:
                raise WorktreeError(f"pr_ref_error: {e}") from e
            existing = self.find_for_pr(pr_number)
            if existing is not None:
                raise WorktreeError(
                    f"pr_worktree_exists: PR #{pr_number} already has worktree "
                    f"{existing.id} at {existing.worktree_path} "
                    f"(status={existing.status.value})."
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
        if pr_number is not None:
            slug = _slugify(label or f"pr-{pr_number}", default=wt_id.replace("wt_", ""))
            branch = f"op/wt/pr-{pr_number}-{wt_id[3:9]}"
        else:
            slug = _slugify(label or "wt", default=wt_id.replace("wt_", ""))
            branch = (branch_name or "").strip() or f"op/wt/{slug}-{wt_id[3:9]}"
        _validate_ref(branch, "branch_name")

        if pr_number is not None:
            # Fetch the PR head into a throwaway local ref first, then
            # point ``git worktree add`` at it below via base_ref —
            # this reuses the exact same add/persist/broadcast path as
            # every other worktree instead of forking a second one.
            fetch_ref = f"op/wt/_pr-{pr_number}-{wt_id[3:9]}"
            try:
                fetch_pr_branch(
                    pr_number, source_repo=source_repo, local_branch=fetch_ref,
                )
            except PrRefError as e:
                raise WorktreeError(f"pr_ref_error: {e}") from e
            base_ref = fetch_ref
        _validate_ref(base_ref, "base_ref")
        # Final worktree path lives outside both source_repo and
        # the chat store so neither can accidentally pick up its files.
        path = Path(destination) if destination else _worktrees_root() / f"{wt_id}-{slug}"
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
        if pr_number is not None:
            # base_ref (the temp fetch ref) is now redundant — `branch`
            # was created pointing at the same commit via `-b`.
            _run_git("branch", "-D", base_ref, cwd=source_repo)

        # .worktreeinclude: copy untracked local-only files (.env,
        # certs, etc.) the fresh checkout never got. No-op when the
        # manifest doesn't exist. Every create_worktree caller (the
        # worktree_create tool, job/plan-mode fan-out) routes through
        # here, so this is the one place that needs the hook.
        include_result = sync_include_files(source_repo, str(path))
        rc_base, base_out, _ = _run_git("rev-parse", "HEAD", cwd=str(path))
        base_sha = base_out.strip() if rc_base == 0 and base_out.strip() else None

        wt = Worktree(
            id=wt_id,
            source_repo=source_repo,
            worktree_path=str(path),
            branch_name=branch,
            base_ref=base_ref,
            status=WorktreeStatus.ACTIVE,
            parent_session=parent_session,
            parent_job=parent_job,
            parent_branch=parent_branch,
            base_sha=base_sha,
            ephemeral=ephemeral,
            pr_number=pr_number,
            include_synced=include_result.copied,
            include_failed=[f"{p}: {reason}" for p, reason in include_result.failed],
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
        parent_job: Optional[str] = None,
    ) -> list[Worktree]:
        return _store_list(
            status_filter=status_filter,
            parent_session=parent_session,
            parent_job=parent_job,
        )

    def find_active_for_session(
        self, session_id: str, branch_id: Optional[str] = None,
    ) -> Optional[Worktree]:
        """The worktree the session's branch runs in (the current turn's
        branch, else the selected one, when ``branch_id`` is omitted)."""
        return find_active_for_session(session_id, branch_id)

    def summarize(self, wt: Worktree) -> dict:
        """What the worktree holds against the commit it started from:
        commits made in it, the files that differ (committed or not,
        untracked included) and whether anything is uncommitted. Empty
        for a worktree whose directory is gone."""
        empty = {"commits": 0, "files": [], "dirty": False, "exists": False}
        path = wt.worktree_path
        if not path or not os.path.isdir(path):
            return empty
        base = wt.base_sha or wt.base_ref or "HEAD"
        rc_c, out_c, _ = _run_git("rev-list", "--count", f"{base}..HEAD", cwd=path)
        commits = int(out_c.strip()) if rc_c == 0 and out_c.strip().isdigit() else 0
        files: list[str] = []
        rc_d, out_d, _ = _run_git("diff", "--name-only", base, cwd=path)
        if rc_d == 0:
            files.extend(ln.strip() for ln in out_d.splitlines() if ln.strip())
        rc_u, out_u, _ = _run_git("ls-files", "--others", "--exclude-standard", cwd=path)
        if rc_u == 0:
            files.extend(ln.strip() for ln in out_u.splitlines() if ln.strip())
        rc_s, out_s, _ = _run_git("status", "--porcelain", cwd=path)
        dirty = rc_s == 0 and bool(out_s.strip())
        return {
            "commits": commits,
            "files": sorted(set(files)),
            "dirty": dirty,
            "exists": True,
        }

    def discard_if_unchanged(self, worktree_id: str) -> Optional[Worktree]:
        """Remove an ephemeral worktree that ended with nothing in it
        (Claude Code's rule for an isolated sub-agent). A worktree with
        any commit or file change stays for the user to merge or
        discard. Returns the discarded row, else ``None``."""
        wt = load_worktree(worktree_id)
        if wt is None or not wt.ephemeral or wt.status != WorktreeStatus.ACTIVE:
            return None
        summary = self.summarize(wt)
        if summary["commits"] or summary["files"] or summary["dirty"]:
            return None
        # Called once its helper ended (the Job's end, or the caller's wait).
        return self.discard_worktree(worktree_id, force=False, delete_branch=True,
                                     owner_done=True)

    def running_owner(self, wt: Worktree) -> Optional[str]:
        """The id of the helper Job this worktree belongs to while it has
        not ended, else None: the folder is still being written to. The
        Jobs engine answers (``set_owner_check``); without it nobody holds."""
        if not wt.parent_job or _owner_check is None:
            return None
        try:
            return _owner_check(wt)
        except Exception:  # noqa: BLE001 - an unreadable Job holds nothing
            logger.debug("owner of worktree %s not read", wt.id, exc_info=True)
            return None

    def _refuse_while_owner_runs(self, wt: Worktree, action: str) -> None:
        running = self.running_owner(wt)
        if running is not None:
            raise WorktreeError(
                f"owner_running: the helper {running} still works in worktree {wt.id}; "
                f"{action} it once that helper has finished, or stop the helper first."
            )

    def merge_worktree(
        self,
        worktree_id: str,
        *,
        strategy: str = "ff-only",
        delete_branch: bool = False,
        commit_pending: bool = False,
        message: Optional[str] = None,
        owner_done: bool = False,
    ) -> Worktree:
        """Merge the worktree's branch back into ``source_repo`` HEAD.

        Refused while the helper Job it belongs to (``parent_job``) has not
        ended (``owner_running``): it may still be writing. ``owner_done``:
        the caller knows that Job has ended.

        ``commit_pending``: the person asked to merge what the worktree
        holds, so uncommitted and untracked changes are committed first
        (``message``, else a generic one). Without it a dirty worktree is
        refused, as before; the agent commits through bash itself.

        ``strategy="auto"``: fast-forward when possible, else a merge
        commit — what the Merge button does, so a source repository that
        moved on since the worktree started still merges.

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
        if not owner_done:
            self._refuse_while_owner_runs(wt, "merge")
        # Refuse if there are uncommitted / untracked changes — agent
        # has to commit explicitly first (D7). ``git status --porcelain``
        # is empty when clean.
        rc, out, _ = _run_git("status", "--porcelain", cwd=wt.worktree_path)
        if rc == 0 and out.strip() and commit_pending:
            _run_git("add", "-A", cwd=wt.worktree_path)
            rc_c, out_c, err_c = _run_git(
                "commit", "-m", (message or "").strip()
                or f"Work from worktree {wt.id} ({wt.branch_name})",
                cwd=wt.worktree_path,
            )
            if rc_c != 0:
                raise WorktreeError(
                    f"commit_failed: {err_c.strip() or out_c.strip()}"
                )
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
            if strategy == "auto":
                rc_ff, _, _ = _run_git(
                    "merge-base", "--is-ancestor", "HEAD", wt.branch_name,
                    cwd=wt.source_repo,
                )
                strategy = "ff-only" if rc_ff == 0 else "no-ff"
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
                if code == "merge_conflict":
                    # Leave the source repository as it was: the
                    # conflict is resolved in the worktree, never by
                    # stranding the user's checkout mid-merge.
                    _run_git("merge", "--abort", cwd=wt.source_repo)
                    if strategy == "squash":
                        _run_git("reset", "--merge", cwd=wt.source_repo)
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
                "diff", "--name-only", f"{wt.base_sha or wt.base_ref}..HEAD",
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
        owner_done: bool = False,
    ) -> Worktree:
        """Tear down the worktree without merging.

        ``force=False`` (default) refuses to discard a dirty worktree;
        the caller has to set ``force=True`` to drop uncommitted work.
        Refused, forced or not, while the helper Job it belongs to has not
        ended; ``owner_done``: the caller knows it has (its end, a spawn
        that failed to start).
        """
        wt = load_worktree(worktree_id)
        if wt is None:
            raise WorktreeError(f"unknown worktree {worktree_id!r}")
        if is_terminal(wt.status):
            # Idempotent — already discarded / merged / kept. Just
            # return the current row.
            return wt
        if not owner_done:
            self._refuse_while_owner_runs(wt, "discard")

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

    # Internals

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


# Module-level singleton

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
