"""SessionStore — sessions as append-only logs.

Public surface matches the 22 methods callers expect (dispatcher /
webui / channels / memory subsystems all import via
``openprogram.runtime.context.session_db.default_db()`` which now returns a
``SessionStore`` instance).

Storage layout per session: see ``store/__init__.py`` module docstring.

Internal model:
  * one ``SessionLog`` per session on disk
  * one ``SessionMemoryIndex`` per session in memory (lazy-loaded)
  * branch names live in the session fields under ``branches: {head_id: name}``
  * context commits are owned by the commit subsystem; this class only
    persists raw nodes + meta.

Message <-> Call dataclass mapping reuses the existing helpers in
``openprogram.state.store._msg_adapter`` so adapter semantics (extra fields,
caller/predecessor routing, ...) stay identical to the SQLite era.
"""


from __future__ import annotations


# Sibling modules (branches, index, messages, node_deletion, service,
# sessions, storage) read their dependencies as ``shared.<name>`` so a test
# can patch one seam (``shared.time``, ``shared.atomic_write_text``) for the
# whole store. Imports only they read carry ``noqa: F401``.
import atexit  # noqa: F401
import json  # noqa: F401
import logging
import os  # noqa: F401
import threading
import time  # noqa: F401
import uuid  # noqa: F401
from collections import OrderedDict  # noqa: F401
from contextvars import ContextVar
from contextlib import contextmanager  # noqa: F401
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional  # noqa: F401


_log = logging.getLogger(__name__)


class SessionPlacementError(RuntimeError):
    """The durable session placement registry could not be consulted."""


_REWIND_RECOVERY_SESSIONS: ContextVar[frozenset[tuple[object, str]]] = ContextVar(
    "openprogram_rewind_recovery_sessions", default=frozenset(),
)
_READING_CACHED_INDEX: ContextVar[bool] = ContextVar("openprogram_reading_cached_index", default=False)


from openprogram.state.store.session.nodes import Call, ROLE_CODE, ROLE_USER, ROLE_LLM  # noqa: F401
from openprogram.host._platform.filesystem import remove_tree  # noqa: F401
from .._msg_adapter import _msg_to_node, _node_to_msg, _row_to_session  # noqa: F401
from ..files.atomic_file import atomic_write_text, read_text_with_retry  # noqa: F401
from ..files.session_log import RUN_ROWS_RECORD, SessionLog, report_foreign_change  # noqa: F401
from ..files.session_state_file import SessionStateFile  # noqa: F401
from ..memory_index import SessionMemoryIndex  # noqa: F401
from ..branch_config import BRANCH_RECORD_KEYS, take_durable_branch_records  # noqa: F401
from ..files.placement import (  # noqa: F401
    INDEX_FILE,
    LOCATIONS_FILE,
    is_deleted,
    iter_session_dirs,
    default_session_dir,
    move_session_folder,
    SessionFolderBusy,
    nested_session_dir,
    quarantine_dir,
    record_delete_intent,
    resolve_existing_dir,
    session_looks_present,
    target_dir_for_project,
)
from ..files.session_lock import (  # noqa: F401
    conversation_lock, registry_file_lock, remove_session_lock_files,
    session_interprocess_lock, stale_lock_files, sweep_lock_files,
)

if TYPE_CHECKING:
    from .service import SessionStore


def _default_root() -> Path:
    """Root of the conversation store: ``<state>/projects``.

    Each project's conversations are in ``<project-id>/chats/`` (see
    :mod:`openprogram.state.store.project.layout`).
    """
    from openprogram.state.store.project.layout import projects_root
    return projects_root()


def _projects_default_id_safe() -> str:
    """The default project id, without touching git. Used as a last
    resort when project resolution failed but we still want the meta to
    carry a project_id pointer."""
    try:
        from openprogram.state.store.project.project_store import DEFAULT_PROJECT_ID
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


def opening_call(inside: str = "/state/store/session/") -> str:
    """The product call outside the store (the path ``inside``) that is reading a session in.

    A session read into memory stays until it is dropped, so a memory sample
    says who brought each one: ``file.py:function`` of the nearest caller
    and of the outermost one on this thread.
    """
    import sys

    frame, calls = sys._getframe(1), []
    while frame is not None:
        name = frame.f_code.co_filename
        if "openprogram" in name and inside not in name:
            calls.append(f"{name.rsplit('/', 1)[-1]}:{frame.f_code.co_name}")
        frame = frame.f_back
    if not calls:
        return "?"
    return calls[0] if len(calls) == 1 else f"{calls[0]} < {calls[-1]}"


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

