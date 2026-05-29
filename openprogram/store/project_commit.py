"""Project-Git auto-commit — wire the agent's file edits into the user's
real repo as attributable commits (entity layer, half 2).

A project-bound session works in the user's actual directory. Every turn
the agent may edit files there. This module makes that turn-end commit
happen so the user gets a ``git log`` of exactly what the agent changed
and can ``git revert`` any of it — the "compare / record what changed"
the entity layer promises.

Two call sites in the dispatcher:

  * :func:`snapshot_baseline` — at turn START, record which paths were
    already dirty (the user's uncommitted work).
  * :func:`commit_turn_changes` — at turn END, commit the agent's edits,
    but refuse (Strategy A) if the user's pre-turn changes are still
    sitting in the tree, so we never fold the user's half-done work into
    an agent commit.

Both are best-effort and fully guarded: a project/git failure must never
break a chat turn. Ad-hoc (default-project) sessions are no-ops — they
have no bound directory, their entity memory is the session repo itself.

**On by default**, but safe (rule A): commits happen only when the bound
folder is already a git repo and the agent actually edited files. We
never ``git init`` the user's folder. Opt out via
``project_auto_commit: false`` in ``~/.openprogram/config.json`` (or env
``OPENPROGRAM_PROJECT_AUTOCOMMIT=0``).

Rule B: when the session has an active git worktree, the agent's edits
live in the worktree copy, so this yields entirely (the worktree has its
own merge/discard lifecycle).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Whether project auto-commit is turned on. **Default ON.**

    Resolution: env ``OPENPROGRAM_PROJECT_AUTOCOMMIT`` (1/true/yes/on or
    0/false/no/off) wins; else ``project_auto_commit`` in config.json;
    else **True**.

    Defaulting ON is safe because of rule A: we only ever commit when the
    bound folder is *already* a git repo (we never create one), and only
    when the agent actually changed files this turn. So for a non-git
    folder or a pure-chat turn this is a silent no-op; the user only sees
    commits when they're working in a real repo and the agent edited it —
    which is exactly when a git-log record is wanted. Set
    ``project_auto_commit: false`` (or the env to 0) to opt out.
    """
    env = os.environ.get("OPENPROGRAM_PROJECT_AUTOCOMMIT", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from openprogram.paths import get_config_path
        cfg = json.loads(get_config_path().read_text(encoding="utf-8"))
        val = cfg.get("project_auto_commit")
        if val is not None:
            return bool(val)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return True


def _project_for(session_id: str):
    """The bound, real (non-default) project for this session, or None.

    Returns a ``Project`` only when the session is tied to an actual
    filesystem directory we should commit into. Ad-hoc / default sessions
    and any resolution failure → None (caller no-ops).
    """
    try:
        from openprogram.store import project_store as _projects
        proj = _projects.project_for_session(session_id)
    except Exception:
        return None
    if proj is None or proj.is_default or not proj.path:
        return None
    if not Path(proj.path).expanduser().is_dir():
        return None
    return proj


def _has_active_worktree(session_id: str) -> bool:
    """Rule B: is there an active git worktree bound to this session?

    When the agent works inside a worktree, its file edits land in the
    isolated worktree copy, NOT the real project dir — so auto-committing
    the real dir would be wrong (empty, or worse, sweep up something
    unrelated). The worktree has its own merge/discard lifecycle; the
    auto-commit yields entirely while one is active. Best-effort: any
    lookup failure → treat as "no active worktree" (don't block commit).
    """
    try:
        from openprogram.worktree.manager import get_manager
        return get_manager().find_active_for_session(session_id) is not None
    except Exception:
        return False


def _notify_not_a_repo(session_id: str, proj, on_event) -> None:
    """One-shot UI notice: auto-commit is on, but the bound folder isn't a
    git repo and we won't create one (rule A). Tells the user how to opt
    in (`git init`). No-op if no on_event sink."""
    if on_event is None:
        return
    try:
        on_event({
            "type": "chat_response",
            "data": {
                "type": "project_commit_skipped",
                "session_id": session_id,
                "project_id": proj.id,
                "path": proj.path,
                "reason": "not_a_git_repo",
                "message": (
                    "Auto-commit is on but this folder isn't a git repo, "
                    "so the agent's edits weren't committed. Run `git init` "
                    "here if you want a git history of agent changes."
                ),
            },
        })
    except Exception:
        pass


def snapshot_baseline(session_id: str) -> Optional[set[str]]:
    """Turn START: the set of paths already dirty in the bound project.

    Passed back to :func:`commit_turn_changes` so Strategy A can tell the
    user's pre-existing work apart from the agent's edits. Returns None
    when there's nothing to track (disabled, ad-hoc session, no repo) —
    callers store it verbatim and hand it back; None means "no baseline".
    """
    if not is_enabled():
        return None
    proj = _project_for(session_id)
    if proj is None:
        return None
    if _has_active_worktree(session_id):
        return None  # rule B: yield to the worktree, don't touch real dir
    try:
        from openprogram.store.project_store import ProjectGit
        pg = ProjectGit(proj.path)
        if not pg.exists():
            # Rule A: the folder isn't a git repo and we will NOT create
            # one. Nothing to baseline; commit_turn_changes will skip.
            return None
        return pg.dirty_paths()
    except Exception as e:  # noqa: BLE001
        logger.debug("project baseline snapshot failed for %s: %s", session_id, e)
        return None


def commit_turn_changes(
    session_id: str,
    user_text: str,
    baseline: Optional[set[str]],
    *,
    on_event=None,
) -> Optional[str]:
    """Turn END: commit the agent's edits in the bound project repo.

    Returns the commit sha on success, or None (nothing to commit /
    disabled / ad-hoc / skipped). When the user had uncommitted work that
    is still present (Strategy A refusal), emits a one-shot warning via
    ``on_event`` so the UI can tell the user their edits weren't swept up
    and the agent's edits are sitting uncommitted alongside them.
    """
    if not is_enabled():
        return None
    proj = _project_for(session_id)
    if proj is None:
        return None
    if _has_active_worktree(session_id):
        return None  # rule B: the worktree owns the edits this turn
    try:
        from openprogram.store.project_store import ProjectGit
        pg = ProjectGit(proj.path)
        # Rule A: never git-init the user's folder. Only commit if it is
        # ALREADY a git repo; otherwise leave it untouched and tell the
        # user (once) that they can `git init` to opt in.
        if not pg.exists():
            _notify_not_a_repo(session_id, proj, on_event)
            return None
        first_line = (user_text or "").strip().splitlines()
        label = (first_line[0][:60] if first_line else "") or "turn"
        msg = f"[agent {session_id[:8]}] {label}"
        result = pg.commit_agent_changes(msg, baseline=baseline)
    except Exception as e:  # noqa: BLE001
        logger.debug("project auto-commit failed for %s: %s", session_id, e)
        return None

    if result == ProjectGit.SKIPPED_DIRTY:
        logger.info(
            "project auto-commit skipped for %s: user has uncommitted "
            "changes in %s", session_id, proj.path,
        )
        if on_event is not None:
            try:
                on_event({
                    "type": "chat_response",
                    "data": {
                        "type": "project_commit_skipped",
                        "session_id": session_id,
                        "project_id": proj.id,
                        "path": proj.path,
                        "reason": "dirty_tree",
                        "message": (
                            "Agent file edits were NOT committed: your "
                            "project has uncommitted changes. The agent's "
                            "edits are in the working tree alongside yours."
                        ),
                    },
                })
            except Exception:
                pass
        return None

    if result:
        logger.info("project auto-commit %s → %s in %s",
                    session_id, result[:8], proj.path)
    return result
