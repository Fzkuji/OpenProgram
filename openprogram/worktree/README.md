# `openprogram/worktree/`

> Agent Worktree subsystem.

## Overview

Provides the ``git worktree`` lifecycle (create / merge / discard / list /
keep) used by agent tools to make isolated changes to the user's real
repository before deciding whether to land them.

Public surface:

  * :class:`Worktree`, :class:`WorktreeStatus` — entity + state machine
    (``openprogram.worktree.types``).
  * :func:`get_manager` — process-wide :class:`WorktreeManager` singleton
    (``openprogram.worktree.manager``).
  * :func:`set_worktree` / :func:`current_worktree_path` — ContextVar
    helpers (``openprogram.worktree.context``).

The worktree directory is **never** placed inside ``~/.agentic/sessions-git/``
— that subtree belongs to OpenProgram's own conversation-memory git
repositories. See ``docs/design/agent-worktree.md`` Part 1 D4.

## Files in this directory

- **`context.py`** — ``_current_worktree_path`` ContextVar
- **`manager.py`** — WorktreeManager
- **`path_resolve.py`** — Path resolution helper for worktree-aware tools
- **`store.py`** — Worktree persistence
- **`types.py`** — Worktree entity + state machine

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
