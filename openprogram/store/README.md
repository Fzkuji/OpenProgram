# `openprogram/store/`

> Git-backed session storage. Replaces the old SQLite ``DagSessionDB`` +

## Overview

``GraphStore`` layer. Everything that used to live in
``openprogram/context/session_db.py`` and ``openprogram/context/storage.py``
moves here.

Public surface (re-exported for legacy import paths):

    SessionStore        ─ main class, implements DagSessionDB-compatible
                          22 public methods on top of git repos
    default_store()     ─ process-wide singleton
    GitSession          ─ per-session git repo wrapper (init / add /
                          commit / log / checkout)
    SessionMemoryIndex  ─ per-session in-memory DAG index, rebuilt
                          from git on startup / cache miss

Storage layout: every session is its own git repo at
``~/.openprogram/sessions/<session_id>/`` (ad-hoc chats) — or inside a
bound project at ``<project>/.openprogram/sessions/<session_id>/``
(indexed by ``sessions/locations.json``). Each repo has two top-level
dirs:

    history/   append-only JSON files, one per DAG node, named
               ``NNNN-{u|a|t|s|...}-<id>.json`` where NNNN is the
               4-digit zero-padded seq. Never modified after write.

    context/   mutable LLM view. ``messages.json`` carries the current
               assembled message list (compact / aging rewrites it);
               ``commits/<id>.json`` carries per-commit ContextItem
               lists, written one file per turn (immutable).

Plus ``meta.json`` at the repo root for session-level fields (title,
agent_id, head_id, ...).

Git is the source of truth. The in-memory index is a query cache,
fully rebuildable from git on demand. See
``docs/design/git-as-entity-memory.md`` for the rationale.

## Files in this directory

- **`_msg_adapter.py`** — Message-dict ⇄ Call-node translation helpers
- **`git_session.py`** — One git repo per session
- **`graphstore_shim.py`** — Backward-compat shim emulating the old ``GraphStore`` API on top of
- **`memory_index.py`** — Per-session in-memory DAG index
- **`project_store.py`** — Project entity memory
- **`search.py`** — Cross-session message search backed by ``ripgrep``
- **`session_store.py`** — SessionStore

## Sub-packages

- **`file_backup/`** — Per-session file-backup store

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
