"""Git-backed storage: session memory, the project entity layer, and the
file revert/record machinery. See ``README.md`` in this directory for the
full map; this docstring covers the original session-storage core.

The directory holds three groups (README has the details):

  1. **Session storage** — one git repo per conversation. The original
     job of this package: ``session_store`` / ``git_session`` /
     ``memory_index`` / ``graphstore_shim`` / ``_msg_adapter`` /
     ``search``. Replaces the old SQLite ``DagSessionDB`` + ``GraphStore``.
  2. **Project entity layer + auto-commit** — ``project_store`` (the
     user's working dir as a git-backed Project, with safe auto-init and
     a reset/revert-of-a-commit primitive) and ``project_commit`` (wires
     the agent's per-turn edits into that repo). See
     ``docs/design/memory/memory-v2.md`` and ``docs/design/runtime/revert-layers.md``.
  3. **Revert / record helpers** — ``checkpoint/`` (per-turn file
     checkpoints = the "undo" layer) and ``read_tracking`` (read-before-
     edit concurrency guard). See ``docs/design/runtime/revert-layers.md``.

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

    context/   the LLM view. ``commits/<id>.json`` carries per-commit
               ContextItem lists, written one immutable file per turn
               by ``context.commit.store.save_commit``. There is no
               ``messages.json`` mirror — every read looks the commit up
               by id / DAG ancestry, not via a shared mutable file.

Plus ``meta.json`` at the repo root for session-level fields (title,
agent_id, head_id, ...).

Git is the source of truth. The in-memory index is a query cache,
fully rebuildable from git on demand. See
``docs/design/memory/git-as-entity-memory.md`` for the rationale.
"""
from contextvars import ContextVar
from typing import Optional, TYPE_CHECKING

# Files live in the session/ project/ snapshot/ sub-packages (group ①②③,
# see README). The top-level package re-exports the historical public
# surface so ``from openprogram.store import SessionStore`` etc. keep
# working unchanged after the physical regroup.
from .session import GitSession, SessionMemoryIndex, GraphStoreShim
# Provenance read-layer — the LLM-free seam memory maps from
# (docs/design/memory/entity-session-cache.md §5).
from .session import (
    Provenance,
    iter_nodes_since,
    node_provenance,
    session_commits,
    project_commits,
    session_project_id,
)

if TYPE_CHECKING:
    pass

# Dispatcher installs a per-turn (SessionStore, session_id) wrapper here
# so deep code (Runtime.exec, ask_user, @agentic_function decorator) can
# write DAG nodes via the same handle without threading args through
# every layer. Default None = standalone, no persistence (still works,
# just writes nothing).
_store: ContextVar[Optional[GraphStoreShim]] = ContextVar(
    "_store", default=None,
)

# Dispatcher installs the current turn's assistant_msg_id here so
# file-mutating tools can resolve which turn to attribute checkpoints
# to (see store/snapshot/checkpoint/helpers.py). Default None =
# no active turn; backup helper becomes a no-op.
_current_turn_id: ContextVar[Optional[str]] = ContextVar(
    "_current_turn_id", default=None,
)

# session_store is imported lazily by default_store() to keep import
# cost low for code paths that only need the lower-level classes.


__all__ = [
    "GitSession",
    "SessionMemoryIndex",
    "GraphStoreShim",
    "SessionStore",
    "default_store",
    "_store",
    "_current_turn_id",
    "Provenance",
    "iter_nodes_since",
    "node_provenance",
    "session_commits",
    "project_commits",
    "session_project_id",
]


def __getattr__(name):
    # Lazy: SessionStore + default_store are pulled in only when
    # actually referenced (most call sites use default_store()).
    if name == "SessionStore":
        from .session.session_store import SessionStore as _S
        return _S
    if name == "default_store":
        from .session.session_store import default_store as _ds
        return _ds
    # Back-compat module aliases after the session/ project/ snapshot/
    # regroup: ``from openprogram.store import project_commit`` (and the
    # other moved modules) keeps resolving to the real submodule in its
    # new sub-package, so callers that used the convenience form don't
    # all need rewriting. These are plain submodule re-exports, not magic.
    _MOVED = {
        "project_store":   "project.project_store",
        "project_commit":  "project.project_commit",
        "read_tracking":   "snapshot.read_tracking",
        "session_store":   "session.session_store",
        "git_session":     "session.git_session",
        "memory_index":    "session.memory_index",
        "graphstore_shim": "session.graphstore_shim",
        "search":          "session.search",
    }
    target = _MOVED.get(name)
    if target:
        import importlib
        return importlib.import_module(f"{__name__}.{target}")
    raise AttributeError(name)
