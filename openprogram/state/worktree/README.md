# `openprogram/state/worktree/`

> Agent Worktree subsystem.

## Overview

Provides the ``git worktree`` lifecycle (create / merge / discard / list /
keep) used by agent tools to make isolated changes to the user's real
repository before deciding whether to land them.

Public surface:

  * :class:`Worktree`, :class:`WorktreeStatus` — entity + state machine
    (``openprogram.state.worktree.types``).
  * :func:`get_manager` — process-wide :class:`WorktreeManager` singleton
    (``openprogram.state.worktree.manager``).
  * :func:`set_worktree` / :func:`current_worktree_path` — ContextVar
    helpers (``openprogram.state.worktree.context``).

The worktree directory is **never** placed inside ``~/.openprogram/projects/``
— that subtree belongs to OpenProgram's own conversation-memory git
repositories. See ``docs/design/runtime/agent-worktree.md`` Part 1 D4.

## Files in this directory

- **`context.py`** — ``_current_worktree_path`` ContextVar
- **`folder_git.py`** — Per-folder Git state for the composer's git pills
- **`include_sync.py`** — ``.worktreeinclude``
- **`manager.py`** — WorktreeManager
- **`path_resolve.py`** — Path resolution helper for worktree-aware tools
- **`pr_ref.py`** — Resolve a PR reference (``123`` / ``#123`` / a GitHub PR URL) into a
- **`store.py`** — Worktree persistence
- **`types.py`** — Worktree entity + state machine

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
