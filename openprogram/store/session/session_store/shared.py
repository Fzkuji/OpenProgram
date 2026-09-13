"""SessionStore — git-backed replacement for the old ``DagSessionDB``.

Public surface matches the 22 methods callers expect (dispatcher /
webui / channels / memory subsystems all import via
``openprogram.agent.session_db.default_db()`` which now returns a
``SessionStore`` instance).

Storage layout per session: see ``store/__init__.py`` module docstring.

Internal model:
  * one ``GitSession`` per session on disk
  * one ``SessionMemoryIndex`` per session in memory (lazy-loaded)
  * branch names live in ``meta.json`` under ``branches: {head_id: name}``
  * context commits are owned by the commit subsystem; this class only
    persists raw nodes + meta.

Message <-> Call dataclass mapping reuses the existing helpers in
``openprogram.store._msg_adapter`` so adapter semantics (extra fields,
caller/predecessor routing, ...) stay identical to the SQLite era.
"""


from __future__ import annotations


import atexit


import json


import logging


import os


import threading


import time


import uuid


from collections import OrderedDict


from contextvars import ContextVar


from contextlib import contextmanager


from pathlib import Path


from typing import Any, Callable, Optional


_log = logging.getLogger(__name__)


class SessionPlacementError(RuntimeError):
    """The durable session placement registry could not be consulted."""


_REWIND_RECOVERY_SESSIONS: ContextVar[frozenset[tuple[object, str]]] = ContextVar(
    "openprogram_rewind_recovery_sessions", default=frozenset(),
)


from openprogram.context.nodes import Call, ROLE_CODE, ROLE_USER, ROLE_LLM


from openprogram._compat import remove_tree


from .._msg_adapter import (
    _msg_to_node,
    _node_to_msg,
    _decode_extra,
    _row_to_session,
)


from ..git_session import GitSession, atomic_write_text, read_text_with_retry


from ..memory_index import SessionMemoryIndex


from ..placement import (
    is_deleted,
    iter_session_dirs,
    default_session_dir,
    nested_session_dir,
    record_delete_intent,
    resolve_existing_dir,
    session_looks_present,
    target_dir_for_project,
)


from ..session_lock import registry_file_lock, session_interprocess_lock


def _default_root() -> Path:
    """Root holding every session repo: ``<state>/sessions/<id>/``.

    Renamed from ``sessions-git`` → ``sessions`` (the ``-git`` suffix
    was an implementation detail leaking into the path). A one-time,
    self-contained rename runs here so existing installs migrate
    transparently — independent of the ``.agentic`` → ``.openprogram``
    migration marker, since a machine may already be past that.
    """
    from openprogram.paths import get_state_dir
    state = Path(get_state_dir())
    new = state / "sessions"
    old = state / "sessions-git"
    if old.exists() and not new.exists():
        try:
            old.rename(new)
        except OSError:
            # Cross-device or perms — fall back to the old location so
            # we never lose the user's sessions.
            return old
    return new


def _projects_default_id_safe() -> str:
    """The default project id, without touching git. Used as a last
    resort when project resolution failed but we still want the meta to
    carry a project_id pointer."""
    try:
        from openprogram.store.project.project_store import DEFAULT_PROJECT_ID
        return DEFAULT_PROJECT_ID
    except ImportError as e:
        _log.debug("project_store unavailable, using literal default id: %s", e)
        return "default"


def _node_conv_predecessor(payload_or_call) -> Optional[str]:
    """Return the conv-chain predecessor of a node (or None).

    dag/overview.md: the edge is the top-level
    ``predecessor`` field — nowhere else.
    """
    if isinstance(payload_or_call, Call):
        return payload_or_call.predecessor or None
    return payload_or_call.get("predecessor") or None


class PredecessorMissingError(ValueError):
    """A ROOT-level conversational node was appended without a
    ``predecessor`` (dag/overview.md write invariant).

    Only the session's first node and spawn branch roots may open a
    new root; anything else would silently fork the session at ROOT.
    """

    def __init__(self, session_id: str, node_id: str):
        super().__init__(
            f"append without predecessor: session={session_id!r} "
            f"node={node_id!r} — ROOT-level conversational nodes must "
            "carry a predecessor (exceptions: session first node, "
            "spawn branch roots via SessionStore.spawn_branch)"
        )
        self.session_id = session_id
        self.node_id = node_id


def _is_hidden_context_node(node) -> bool:
    """``context/*`` machinery that stays out of chat/transcript views.

    §7 reserves the ``context/*`` name for nodes that record what the
    pipeline sent (``context/system_prompt``) rather than what was said.
    ``context/summary`` is deliberately NOT hidden: §8 makes it an
    ordinary chain member whose output is real conversation content
    standing in for the range it covers.
    """
    name = str(getattr(node, "name", "") or "")
    if not name.startswith("context/"):
        return False
    return name != "context/summary"


def _is_spawn_root(meta: dict) -> bool:
    return bool(meta.get("spawn_branch_root")) or meta.get("source") == "agent_spawn"


class BrokenPredecessorChainError(ValueError):
    """``get_branch`` hit a node with no ``predecessor`` that is not a
    legal branch terminus (spawn root / ROOT / session first node).
    No heuristics — broken data raises instead of being guessed at."""

    def __init__(self, session_id: str, node_id: str):
        super().__init__(
            f"broken predecessor chain: session={session_id!r} "
            f"node={node_id!r} has no predecessor and is not a spawn "
            "branch root, ROOT, or the session's first node"
        )
        self.session_id = session_id
        self.node_id = node_id


def _is_first_conv_node(idx, node) -> bool:
    """True iff ``node`` is the session's earliest ROOT-level
    conversational node — the one place a missing predecessor is a
    legal terminus."""
    node_seq = node.seq if hasattr(node, "seq") else -1
    for rn in idx.nodes_by_seq:
        rn_seq = rn.seq if hasattr(rn, "seq") else -1
        if rn_seq >= node_seq:
            return True
        if (_node_caller(rn) or "") not in ("", "ROOT"):
            continue
        if (rn.metadata or {}).get("display") == "root":
            continue
        if rn.role in (ROLE_USER, ROLE_LLM):
            return False
    return True


def _check_append_invariant(session_id: str, idx, node: Call,
                            predecessor: Optional[str],
                            caller: Optional[str]) -> None:
    """Raise ``PredecessorMissingError`` on an illegal ROOT-level append."""
    if node.role not in (ROLE_USER, ROLE_LLM):
        return
    if node.role == ROLE_USER and node.input is not None:
        return                       # ask_user answer node — a callee, not a turn
    if caller and caller != "ROOT":
        return                       # sub-call / spawn root (caller=spawning node)
    if predecessor:
        return
    meta = node.metadata or {}
    if meta.get("display") == "root":
        return                       # the ROOT node itself
    if _is_spawn_root(meta):
        return
    # §8: a summary node normally carries the predecessor of the first
    # node it covers and needs no exemption at all. The one legal
    # predecessor-less case is compacting from the very start of a
    # session, where the covered range begins at the first node — the
    # summary inherits its (empty) predecessor and becomes the new chain
    # terminus. ``k_`` clones no longer exist, so no exemption for them.
    if meta.get("covers_ids") is not None:
        return
    # Session first node: no prior ROOT-level conversational node.
    for n in idx.nodes_by_seq:
        nm = n.metadata or {}
        if nm.get("display") == "root":
            continue
        if n.role in (ROLE_USER, ROLE_LLM) and (not n.caller or n.caller == "ROOT"):
            raise PredecessorMissingError(session_id, node.id)


def _node_caller(payload_or_call) -> Optional[str]:
    if isinstance(payload_or_call, Call):
        return payload_or_call.caller or None
    return payload_or_call.get("caller") or None


_DEFAULT_CACHE_CAP = 256


def _resolve_cache_cap() -> int:
    raw = (os.environ.get("OPENPROGRAM_SESSION_CACHE_CAP") or "").strip()
    if not raw:
        return _DEFAULT_CACHE_CAP
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_CACHE_CAP


_default_lock = threading.Lock()


_default_store: Optional[SessionStore] = None


def default_store() -> SessionStore:
    from .service import SessionStore
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = SessionStore()
    return _default_store

