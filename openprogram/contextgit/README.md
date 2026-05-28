# `openprogram/contextgit/`

> ContextGit — context as a git repo.

## Overview

See ``docs/design/contextgit.md`` for the full design. TL;DR:

- Every conversation is a DAG of "commits" (user messages, assistant
  replies, function runs). Each commit has a ``parent_id``; siblings
  (same parent) represent retries / edits / alternate versions.
- A conversation carries a ``head_id`` — the commit currently displayed.
- Switching ``head_id`` (checkout) is pure UI; nothing re-executes.
- Commits are append-only. Edits and retries never mutate; they create
  sibling commits.

The v1 implementation is *not* a separate persistent object store yet —
it's DAG metadata layered on top of the existing conversation messages
dict (see :mod:`openprogram.webui.server`) and the existing
:class:`~openprogram.webui.messages.MessageStore`. Each message dict
gets a ``parent_id`` field (optional; legacy messages default to their
list-order predecessor on load) and each conversation carries
``head_id``.

This module exposes the pure DAG helpers — sibling lookup, linear
history walk, checkout validation — so both the server and any future
CLI tooling can share one implementation. No I/O lives here.

## Files in this directory

- **`dag.py`** — Pure DAG helpers over a list of message dicts

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
