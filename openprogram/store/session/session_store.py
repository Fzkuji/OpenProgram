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
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

from openprogram.context.nodes import Call, ROLE_USER, ROLE_LLM
# Adapter functions (msg-dict <-> Call) — reused unchanged so SQLite-era
# tests covering edge cases (sub-call routing, extra_json roundtrip) still hold.
from ._msg_adapter import (
    _msg_to_node,
    _node_to_msg,
    _decode_extra,
    _row_to_session,
)

from .git_session import GitSession, atomic_write_text, read_text_with_retry
from .memory_index import SessionMemoryIndex


# Paths


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


# Edge resolvers
# A node has two distinct parent edges:
#   * caller       ─ Call.caller       (the LLM/ROOT that invoked a sub-call)
#   * predecessor  ─ Call.predecessor  (the conversation-chain parent)
# Both are top-level schema fields — dag/overview.md §3 forbids a
# metadata mirror for either.
# Keep these as standalone functions so the index doesn't know about
# message-dict field layout.


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


# Cache bound
# The in-memory ``SessionMemoryIndex`` cache is pure (rebuildable from
# git), so it can be size-capped without losing data: an evicted session
# is transparently rebuilt from disk on next access. Without a cap the
# cache only grows — a single ``list_sessions`` (followed by per-session
# stats like ``count_recent_nodes``) loads every session that ever
# existed and pins them in RAM for the worker's lifetime. The cap is an
# LRU bound: the most-recently-opened session is always at the MRU end,
# so an in-flight turn (just touched) is never the one evicted, and idle
# sessions fall off the LRU end. Generous by default — this is a memory-
# leak backstop, not a hot-path constraint.
_DEFAULT_CACHE_CAP = 256


def _resolve_cache_cap() -> int:
    raw = (os.environ.get("OPENPROGRAM_SESSION_CACHE_CAP") or "").strip()
    if not raw:
        return _DEFAULT_CACHE_CAP
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_CACHE_CAP


# SessionStore


class SessionStore:
    """Git-backed session store.

    One instance per process (use ``default_store()``). Methods are
    thread-safe — per-session locks live on the GitSession / index.

    ``root_path`` is the directory holding per-session git repos.
    """

    def __init__(
        self,
        root_path: Optional[Path] = None,
        *,
        cache_cap: Optional[int] = None,
    ) -> None:
        self.root_path = Path(root_path).expanduser() if root_path else _default_root()
        self.root_path.mkdir(parents=True, exist_ok=True)
        # Cache: session_id → (GitSession, SessionMemoryIndex). Lazy,
        # LRU-ordered (OrderedDict: insertion/access order = recency), and
        # size-capped at ``_cache_cap`` — see the _DEFAULT_CACHE_CAP note.
        self._sessions: "OrderedDict[str, tuple[GitSession, SessionMemoryIndex]]" = OrderedDict()
        self._cache_cap = cache_cap if cache_cap is not None else _resolve_cache_cap()
        self._lock = threading.Lock()
        # Filesystem work is serialized per session, not across the store.
        # ponytail: retain one small RLock per seen session id; replace with a
        # bounded lock table only if profiles with millions of ids appear.
        self._session_locks: dict[str, Any] = {}
        self._locations_write_lock = threading.Lock()
        # Location index: session_id → absolute repo path, for sessions
        # that live OUTSIDE the home root (i.e. inside a bound project's
        # ``<project>/.openprogram/sessions/<id>/``). Sessions absent
        # from this index resolve to the home root, exactly as before —
        # so existing installs see zero behaviour change.
        self._locations: dict[str, str] = self._load_locations()
        # Registry: session_id → summary dict. Loaded from index.json at
        # startup, kept in sync by create/update/delete/append_message.
        # list_sessions reads only this — no disk I/O.
        self._index: dict[str, dict[str, Any]] = {}
        self._index_dirty = False
        self._index_timer: Optional[threading.Timer] = None
        self._index_generation = 0
        self._index_lock = threading.Lock()
        self._index_write_lock = threading.Lock()
        self._load_index()
        atexit.register(self._flush_index)

    # Location index (per-project session placement)

    def _locations_path(self) -> Path:
        return self.root_path / "locations.json"

    def _load_locations(self) -> dict[str, str]:
        p = self.root_path / "locations.json"
        if not p.exists():
            return {}
        try:
            data = json.loads(read_text_with_retry(p))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_locations(self, locations: dict[str, str]) -> None:
        try:
            atomic_write_text(
                self._locations_path(),
                json.dumps(locations, indent=2, ensure_ascii=False),
            )
        except OSError as e:
            _log.warning("locations.json NOT saved (%s); session placement "
                         "may be lost on restart", e)

    def _record_location(self, session_id: str, repo_dir: Path) -> None:
        """Persist that ``session_id``'s repo lives at ``repo_dir`` (an
        absolute path outside the home root). Idempotent."""
        with self._session_lock(session_id):
            with self._locations_write_lock:
                with self._lock:
                    self._locations[session_id] = str(repo_dir)
                    snapshot = dict(self._locations)
                self._save_locations(snapshot)

    def relocate_project_sessions(self, session_ids, new_project_path) -> int:
        """Rewrite the location index after a project moved on disk.

        Called by ``project_store.relocate_project`` for every session
        bound to the moved project. Only sessions that already have a
        location entry are rewritten — ad-hoc sessions living in the
        home root stay put. Cached repo objects are dropped so the next
        open reloads from the new path. Returns the rewrite count.
        """
        base = Path(new_project_path).expanduser()
        moved = 0
        for sid in session_ids or []:
            with self._session_lock(sid):
                with self._lock:
                    if sid not in self._locations:
                        continue
                    self._sessions.pop(sid, None)
                self._record_location(
                    sid, base / ".openprogram" / "sessions" / sid)
            moved += 1
        return moved

    def _forget_location(self, session_id: str) -> None:
        """删会话时移除位置映射（配对 _record_location）。"""
        with self._session_lock(session_id):
            with self._locations_write_lock:
                with self._lock:
                    if self._locations.pop(session_id, None) is None:
                        return
                    snapshot = dict(self._locations)
                self._save_locations(snapshot)

    # Registry (index.json)

    _INDEX_FIELDS = frozenset({
        "id", "agent_id", "title", "created_at", "updated_at",
        "source", "channel", "account_id", "peer_display", "peer_id",
        "pinned", "archived", "group", "status", "unread",
    })

    def _index_path(self) -> Path:
        return self.root_path / "index.json"

    def _load_index(self) -> None:
        p = self._index_path()
        if p.exists():
            try:
                data = json.loads(read_text_with_retry(p))
                if isinstance(data, dict):
                    self._index = data
                    self._reset_running_status()
                    self._startup_cleanup()
                    return
            except (OSError, json.JSONDecodeError):
                _log.warning("index.json corrupted, rebuilding from meta.json files")
        self._rebuild_index()
        self._startup_cleanup()

    def _rebuild_index(self) -> None:
        self._index = {}
        seen: set[str] = set()
        dirs_to_scan: list[Path] = []
        if self.root_path.exists():
            for sdir in self.root_path.iterdir():
                if sdir.is_dir() and (sdir / "meta.json").exists():
                    dirs_to_scan.append(sdir)
                    seen.add(sdir.name)
        for sid, loc in self._locations.items():
            if sid not in seen:
                p = Path(loc)
                if p.is_dir() and (p / "meta.json").exists():
                    dirs_to_scan.append(p)
        for sdir in dirs_to_scan:
            try:
                meta = json.loads(read_text_with_retry(sdir / "meta.json"))
                sid = meta.get("id") or sdir.name
                entry = self._meta_to_entry(meta)
                if not entry.get("created_at"):
                    try:
                        entry["created_at"] = (sdir / "meta.json").stat().st_mtime
                    except OSError:
                        pass
                # Backfill preview from history.
                try:
                    text = self.latest_user_text(sid)
                    if text:
                        text = text.strip().replace("\n", " ")
                        entry["preview"] = (text[:77] + "…") if len(text) > 80 else text
                except Exception as e:  # noqa: BLE001 — preview is cosmetic
                    _log.debug("preview backfill failed for %s: %s", sid, e)
                self._index[sid] = entry
            except (OSError, json.JSONDecodeError):
                continue
        self._reset_running_status()
        self._save_index()

    def _reset_running_status(self) -> None:
        for entry in self._index.values():
            if entry.get("status") == "running":
                entry["status"] = "idle"

    _EMPTY_SHELL_AGE = 3600       # 1 hour
    _ARCHIVE_EXPIRY = 90 * 86400  # 90 days
    _CAPACITY_LIMIT = 1000

    def _startup_cleanup(self) -> None:
        now = time.time()
        to_delete: list[str] = []
        for sid, entry in list(self._index.items()):
            created = entry.get("created_at") or 0
            updated = entry.get("updated_at") or created
            sdir = self._session_dir(sid)
            # A project-bound session whose recorded location is
            # unreachable (the project folder moved and is not yet
            # relocated) is NOT an empty shell — the repo exists
            # somewhere else on disk. Deleting it here would purge the
            # session the moment the worker restarts after a move.
            if sdir != self.root_path / sid and not sdir.exists():
                continue
            # Empty shells: no history, older than 1 hour.
            if (now - created) > self._EMPTY_SHELL_AGE:
                has_history = (sdir / "history").is_dir() and any(
                    (sdir / "history").iterdir()
                ) if (sdir / "history").exists() else False
                if not has_history:
                    to_delete.append(sid)
                    continue
            # Expired archives.
            if entry.get("archived") and (now - updated) > self._ARCHIVE_EXPIRY:
                to_delete.append(sid)
        for sid in to_delete:
            self._index.pop(sid, None)
            shutil.rmtree(self._session_dir(sid), ignore_errors=True)
            self._forget_stale_bindings(sid)
        # Capacity: trim oldest archived sessions beyond the limit.
        dirty = bool(to_delete)
        if len(self._index) > self._CAPACITY_LIMIT:
            archived = [(s, e) for s, e in self._index.items() if e.get("archived")]
            archived.sort(key=lambda x: x[1].get("updated_at") or 0)
            excess = len(self._index) - self._CAPACITY_LIMIT
            for sid, _ in archived[:excess]:
                self._index.pop(sid, None)
                shutil.rmtree(self._session_dir(sid), ignore_errors=True)
                self._forget_stale_bindings(sid)
                dirty = True
        if dirty:
            self._save_index()

    def _forget_stale_bindings(self, session_id: str) -> None:
        """Same location + project cleanup delete_session does — startup
        cleanup removed the directory but left the locations map and the
        project reverse index pointing at it, so both grew stale entries
        forever."""
        self._forget_location(session_id)
        try:
            from openprogram.store.project import project_store as _projects
            _projects.unbind_session(session_id)
        except Exception as e:  # noqa: BLE001 — reverse index is best-effort
            _log.warning("session %s NOT unbound from its project: %s",
                         session_id, e)

    def _meta_to_entry(self, meta: dict) -> dict:
        entry = {}
        for k in self._INDEX_FIELDS:
            if k in meta:
                entry[k] = meta[k]
        entry.setdefault("id", meta.get("id", ""))
        entry.setdefault("status", "idle")
        entry.setdefault("pinned", False)
        entry.setdefault("archived", False)
        entry.setdefault("unread", False)
        return entry

    def _save_index(self) -> None:
        with self._index_write_lock:
            with self._index_lock:
                snapshot = {sid: dict(entry) for sid, entry in self._index.items()}
                generation = self._index_generation
            try:
                atomic_write_text(
                    self._index_path(),
                    json.dumps(snapshot, indent=2, ensure_ascii=False,
                               default=str),
                )
            except OSError as e:
                _log.warning("index.json NOT saved (%s); session list may be "
                             "stale until the next rebuild", e)
                with self._index_lock:
                    self._index_dirty = True
                return
            with self._index_lock:
                if self._index_generation == generation:
                    self._index_dirty = False

    def _schedule_index_flush(self) -> None:
        with self._index_lock:
            self._index_dirty = True
            if self._index_timer is not None:
                return
            self._index_timer = threading.Timer(5.0, self._do_deferred_flush)
            self._index_timer.daemon = True
            self._index_timer.start()

    def _do_deferred_flush(self) -> None:
        with self._index_lock:
            self._index_timer = None
            dirty = self._index_dirty
        if dirty:
            self._save_index()

    def _flush_index(self) -> None:
        with self._index_lock:
            if self._index_timer is not None:
                self._index_timer.cancel()
                self._index_timer = None
            dirty = self._index_dirty
        if dirty:
            self._save_index()

    def _update_index_entry(self, session_id: str, **fields: Any) -> None:
        with self._index_lock:
            entry = self._index.get(session_id)
            if entry is None:
                entry = {"id": session_id, "status": "idle", "pinned": False,
                         "archived": False, "unread": False}
                self._index[session_id] = entry
            # updated_at 只由调用方显式传入（追加消息的路径）——改名/置顶/
            # 标已读不算"最新一次聊天"，不许把会话顶到侧栏最上。
            for k, v in fields.items():
                if k in self._INDEX_FIELDS or k == "preview":
                    entry[k] = v
            self._index_generation += 1
            self._index_dirty = True

    # Internals

    def _session_lock(self, session_id: str):
        with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.RLock()
                self._session_locks[session_id] = lock
            return lock

    def _session_dir(self, session_id: str) -> Path:
        """Where ``session_id``'s git repo lives.

        Project-bound sessions live inside their project at
        ``<project>/.openprogram/sessions/<id>/`` (recorded in the
        location index). Everything else — ad-hoc chats, all
        pre-existing sessions — resolves to the home root
        ``<state>/sessions/<id>/``.

        The location index is a snapshot taken at create time, so it
        goes stale when the project folder moves on disk. When the
        recorded path no longer holds a repo, follow the project
        registry (which relocate / auto-claim keeps current), and heal
        the index if the repo is found at the project's current path.
        """
        with self._lock:
            loc = self._locations.get(session_id)
        if not loc:
            return self.root_path / session_id
        p = Path(loc)
        if (p / "history").is_dir():
            return p
        try:
            from openprogram.store.project import project_store as _projects
            proj = _projects.project_for_session(session_id)
            if proj and not proj.is_default and proj.path:
                cand = (Path(proj.path).expanduser()
                        / ".openprogram" / "sessions" / session_id)
                if cand != p and (cand / "history").is_dir():
                    self._record_location(session_id, cand)
                    return cand
        except Exception as e:  # noqa: BLE001 — healing is best-effort
            _log.warning("stale location for %s not healed: %s", session_id, e)
        return p

    def _open(self, session_id: str, *, create_if_missing: bool = False) -> Optional[tuple[GitSession, SessionMemoryIndex]]:
        """Return (git, idx). Loads from disk on first access. None if
        session doesn't exist and ``create_if_missing`` is False."""
        if not create_if_missing and (
            not isinstance(session_id, str)
            or not session_id
            or session_id in {".", ".."}
            or "/" in session_id
            or "\\" in session_id
        ):
            return None
        with self._session_lock(session_id):
            verified_git: GitSession | None = None
            sdir = self._session_dir(session_id)
            with self._lock:
                cached = self._sessions.get(session_id)
                if cached:
                    self._sessions.move_to_end(session_id)
            if not create_if_missing:
                verified_git = GitSession(sdir)
                if (
                    sdir.is_symlink()
                    or not verified_git.exists()
                    or not (sdir / "history").is_dir()
                ):
                    return None
            if cached:
                git, idx = cached
                # @agentic_function runs execute in a fork()'d subprocess
                # that appends history and moves HEAD on disk directly —
                # this process's cached index can't see that, so a
                # mid-run load_session answered from stale memory (the
                # transcript kept showing the previous run until turn
                # end). Two stat calls detect the foreign write; rebuild
                # from disk when it happened. Own-process writes call
                # mark_synced(), so they never trigger a rebuild.
                with idx._persist_lock:
                    if git.exists() and git.stale():
                        # Rebuild IN PLACE (reset + repopulate the SAME index
                        # object): callers across a multi-step operation hold
                        # (git, idx) from an earlier _open — swapping the
                        # cached object would orphan their reference, and a
                        # later step that re-opens (e.g. commit_turn) would
                        # persist the rebuilt object's stale head over their
                        # in-memory update.
                        disk_meta = git.read_meta()
                        disk_hist = git.list_history()
                        # A concurrent reader can land here between
                        # create_session's set_meta() and its _persist_meta()
                        # — the repo exists (some other write initialized it)
                        # but meta.json is still empty/absent on disk. Blindly
                        # rebuilding then resets the index and throws away the
                        # just-populated in-memory meta (source/channel/peer_*
                        # silently vanished). Disk with nothing in it is never
                        # a more truthful view than populated memory, so skip
                        # the rebuild instead of destroying state.
                        if not disk_meta and not disk_hist and (idx.meta or idx.head_id):
                            pass
                        else:
                            idx.rebuild_from_paths(
                                disk_hist,
                                disk_meta,
                                _node_conv_predecessor,
                                _node_caller,
                            )
                            git.mark_synced()
                return cached
            if not sdir.exists() and not create_if_missing:
                return None
            git = verified_git or GitSession(sdir)
            idx = SessionMemoryIndex()
            if git.exists():
                idx.rebuild_from_paths(
                    git.list_history(),
                    git.read_meta(),
                    _node_conv_predecessor,
                    _node_caller,
                )
                git.mark_synced()
            with self._lock:
                # New key lands at the MRU (end) of the OrderedDict.
                self._sessions[session_id] = (git, idx)
                # Evict least-recently-used entries beyond the cap. The
                # just-inserted session is at the MRU end, so popitem(last=
                # False) (oldest) never evicts it as long as cap >= 1. An
                # evicted index rebuilds losslessly from git on next access;
                # an in-flight turn keeps its own (git, idx) reference and is
                # unaffected by being dropped from this dict.
                while len(self._sessions) > self._cache_cap:
                    self._sessions.popitem(last=False)
            return git, idx

    @contextmanager
    def _head_file_lock(self, git: GitSession):
        """Serialize every durable meta/HEAD writer across processes."""
        from openprogram import _compat as fcntl
        import hashlib

        lock_root = git.path.parent / ".session-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(str(git.path).encode()).hexdigest()[:24]
        with (lock_root / f"{lock_name}.head.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _persist_meta(self, git: GitSession, idx: SessionMemoryIndex) -> None:
        """Sync the in-memory meta back to ``meta.json``. Called whenever
        title / head_id / extra / branches change."""
        with self._head_file_lock(git):
            with idx._persist_lock:
                with idx._lock:
                    meta = dict(idx.meta)
                    meta["head_id"] = idx.head_id
                git.write_meta(meta)

    def session_workdir(self, session_id: str) -> Optional[Path]:
        """Path of the per-session scratch workdir, materialized on first
        write. None if the session doesn't exist yet."""
        pair = self._open(session_id)
        if not pair:
            return None
        git, _ = pair
        git._ensure_init()
        return git.workdir_path

    def commit_turn(self, session_id: str, message: str) -> Optional[str]:
        """Commit the current working tree as one turn. Public so the
        dispatcher can call it at turn end; also called internally by
        write paths that don't need an explicit commit boundary.

        Repo layout is append-only by design — no mutable "current
        state" mirror file: history/ holds per-node files, context/
        commits/ holds per-commit files, meta.json carries session-
        level scalars (head_id is a UI pointer, single-valued by
        construction). Two agents writing concurrently never target
        the same file. Refresh meta only here.

        Returns commit sha or None if nothing to commit.
        """
        pair = self._open(session_id)
        if not pair:
            return None
        git, idx = pair
        self._persist_meta(git, idx)
        return git.commit_all(message)

    # Session CRUD

    def create_session(
        self,
        session_id: str,
        agent_id: str,
        *,
        title: str = "",
        source: Optional[str] = None,
        channel: Optional[str] = None,
        peer_display: Optional[str] = None,
        peer_id: Optional[str] = None,
        **other_fields: Any,
    ) -> None:
        # Pull out the project hints BEFORE opening the repo, because
        # they decide WHERE the repo lives (home vs inside a project).
        project_id = other_fields.pop("project_id", None)
        project_path = other_fields.pop("project_path", None)
        # The per-session ``work_dir`` (set by the user via the picker
        # at the top of the chat, stored on the conversation meta) IS
        # the project directory. If the caller didn't pass an explicit
        # ``project_path``, treat ``work_dir`` as the project to bind.
        # NB: we ``get`` (not ``pop``) work_dir — it stays on the meta
        # so ``resolve_work_dir`` keeps reading it for agent file ops.
        if not project_path:
            _wd = other_fields.get("work_dir")
            if isinstance(_wd, str) and _wd.strip():
                project_path = _wd.strip()

        # Resolve the project + decide the session's home on disk
        # Every session belongs to a project (entity layer, half 2 —
        # docs/design/memory/overview.md §2):
        #   * caller passed ``project_path`` (a real dir) → that dir is
        #     the project; the session repo lives INSIDE it at
        #     ``<dir>/.openprogram/sessions/<id>/`` and we record the
        #     location so later reads find it.
        #   * caller passed ``project_id`` of a real (non-default)
        #     project → same, using that project's stored path.
        #   * neither, or the default project → ad-hoc: the session
        #     stays in the home root and just carries
        #     ``project_id="default"`` as a grouping label.
        # All guarded — a project/git failure must never block session
        # creation; we degrade to the home root.
        try:
            from openprogram.store.project import project_store as _projects
            if project_path:
                proj = _projects.resolve_project(project_path)
            elif project_id and project_id != _projects.DEFAULT_PROJECT_ID:
                proj = _projects.get_project(project_id) or _projects.get_default_project()
            else:
                proj = _projects.get_default_project()
            project_id = proj.id
            # Non-default project with a real path → relocate the
            # session repo inside the project dir.
            if (not proj.is_default) and proj.path:
                repo_dir = Path(proj.path).expanduser() / ".openprogram" / "sessions" / session_id
                self._record_location(session_id, repo_dir)
        except Exception as e:  # noqa: BLE001 — never block session creation
            _log.warning("project resolution failed for %s (%s); falling back "
                         "to the default project", session_id, e)
            project_id = project_id or _projects_default_id_safe()

        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        if idx.meta.get("id") == session_id:
            return  # Already created
        extra: dict[str, Any] = {}
        if channel:
            extra["channel"] = channel
        if peer_display:
            extra["peer_display"] = peer_display
        if peer_id:
            extra["peer_id"] = peer_id
        for k, v in other_fields.items():
            if v is not None:
                extra[k] = v
        now = time.time()
        # Caller-supplied created_at/updated_at (e.g. channel replay)
        # take precedence over the default ``now``; explicitly pop
        # them from ``extra`` so the **extra spread doesn't collide
        # with the named kwargs.
        created_at = extra.pop("created_at", now)
        updated_at = extra.pop("updated_at", now)

        if project_id:
            extra["project_id"] = project_id

        idx.set_meta(
            id=session_id,
            agent_id=agent_id,
            title=title,
            source=source or "",
            created_at=created_at,
            updated_at=updated_at,
            **extra,
        )
        self._persist_meta(git, idx)

        # Write registry entry.
        _explicit = {"agent_id", "title", "source", "created_at",
                     "updated_at", "channel", "peer_display", "peer_id", "status"}
        self._update_index_entry(
            session_id,
            agent_id=agent_id,
            title=title,
            source=source or "",
            created_at=created_at,
            updated_at=updated_at,
            channel=channel,
            peer_display=peer_display,
            peer_id=peer_id,
            status="idle",
            **{k: v for k, v in extra.items()
               if k in self._INDEX_FIELDS and k not in _explicit},
        )
        self._save_index()

        # Record the reverse index (project → sessions). Also
        # best-effort.
        if project_id:
            try:
                from openprogram.store.project import project_store as _projects
                _projects.bind_session(session_id, project_id)
            except Exception as e:  # noqa: BLE001 — reverse index is best-effort
                _log.warning("session %s NOT bound to project %s: %s",
                             session_id, project_id, e)

    def update_session(self, session_id: str, **fields: Any) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        # head_id needs special routing because it's also the index's
        # ``head_id`` field.
        if "head_id" in fields and fields["head_id"] is not None:
            idx.set_head(fields.pop("head_id"))
        # Drop Nones so we don't clobber existing fields with NULL.
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            idx.set_meta(**clean)
        self._persist_meta(git, idx)
        # Sync registry.
        index_fields = {k: v for k, v in clean.items()
                        if k in self._INDEX_FIELDS}
        if index_fields:
            self._update_index_entry(session_id, **index_fields)
            self._save_index()

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        pair = self._open(session_id)
        if pair is None:
            return None
        git, idx = pair
        # Synthesize a row-shaped dict so _row_to_session can format it
        # like the old SQLite path.
        meta = dict(idx.meta)
        extra = {k: v for k, v in meta.items() if k not in {
            "id", "title", "agent_id", "source", "model",
            "created_at", "updated_at", "head_id", "last_node_id",
        }}
        row = {
            "id": meta.get("id") or session_id,
            "title": meta.get("title", ""),
            "agent_id": meta.get("agent_id", ""),
            "source": meta.get("source"),
            "model": meta.get("model"),
            "created_at": meta.get("created_at", 0),
            "updated_at": meta.get("updated_at", 0),
            "last_node_id": idx.head_id,
            "extra_json": json.dumps(extra, default=str),
        }
        return _row_to_session(row)

    def delete_session(self, session_id: str) -> None:
        with self._session_lock(session_id):
            with self._lock:
                pair = self._sessions.pop(session_id, None)
                loc = self._locations.get(session_id)
            if pair:
                pair[0].destroy()
            else:
                GitSession(Path(loc) if loc else self.root_path / session_id).destroy()
            with self._index_lock:
                if self._index.pop(session_id, None) is not None:
                    self._index_generation += 1
                    self._index_dirty = True
            self._save_index()
            # 位置映射（配对 _record_location）。
            self._forget_location(session_id)
            # 从项目反向索引解绑（配对 bind_session），避免 session_ids 只增不减。
            try:
                from openprogram.store.project import project_store as _projects
                _projects.unbind_session(session_id)
            except Exception as e:  # noqa: BLE001 — reverse index is best-effort
                _log.warning("session %s NOT unbound from its project: %s",
                             session_id, e)

    def list_sessions(
        self,
        *,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        source: Optional[str] = None,
        include_archived: bool = False,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Registry rows, newest activity first.

        Archived sessions are hidden by default — archiving exists so a
        list stops growing without end, which only works if the default
        list honours it. Maintenance passes that must visit every
        session (memory scan, run-state repair, commit lookup) pass
        ``include_archived=True``; an explicit ``archived=`` filter
        selects one side on its own.
        """
        with self._index_lock:
            rows = [dict(row) for row in self._index.values()]
        if agent_id is not None:
            rows = [r for r in rows if r.get("agent_id") == agent_id]
        if source is not None:
            rows = [r for r in rows if r.get("source") == source]
        if not include_archived and "archived" not in filters:
            rows = [r for r in rows if not r.get("archived")]
        for k, v in filters.items():
            if v is not None:
                rows = [r for r in rows if r.get(k) == v]
        rows.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
        return rows[offset:offset + limit]

    def set_archived(self, session_id: str, archived: bool) -> bool:
        """Flip a session's archive flag. Returns False if unknown.

        Pure metadata: no node is touched and ``updated_at`` stays put
        (index-consistency.html — only appending a message is activity),
        so archiving is fully reversible and never reorders the list.
        """
        with self._index_lock:
            known = session_id in self._index
        if not known and self._open(session_id) is None:
            return False
        self.update_session(session_id, archived=bool(archived))
        return True

    def count_sessions(
        self,
        *,
        agent_id: Optional[str] = None,
        source: Optional[str] = None,
        include_archived: bool = False,
    ) -> int:
        return len(self.list_sessions(
            agent_id=agent_id, source=source,
            include_archived=include_archived, limit=10**9,
        ))

    def invalidate_cache(self, session_id: str) -> None:
        """Drop the in-memory ``SessionMemoryIndex`` for ``session_id`` so
        the next ``_open`` rebuilds it from disk.

        Needed because @agentic_function tools run in a spawn()'d
        subprocess (see ``openprogram/agent/process_runner.py``) that
        writes Call nodes directly to the per-session git history with
        its OWN ``SessionStore`` instance. The parent worker's cached
        index never observes those writes, so ``get_messages`` /
        ``build_branches_payload`` keep returning the pre-subprocess
        snapshot — which is missing every nested code/tool node (the
        gui_agent square + its gui_step / conclusion / exec children).

        Cheap: O(history length) git directory listing on next access.
        """
        with self._session_lock(session_id):
            with self._lock:
                self._sessions.pop(session_id, None)

    # Message append / read

    def spill_large_node(self, session_id: str, node) -> None:
        """Write an over-cap node's full text to ``large_nodes/`` and stamp
        ``metadata.spilled`` on it, before the node is written to history.

        Recording is the one moment a node's text is genuinely new, so it
        is the one place spilling belongs — rendering stays a pure read.
        Must run AFTER seq assignment (the seq names the file) and BEFORE
        ``write_history`` (so the stamp lands in the persisted node).
        """
        try:
            from openprogram.context import spill as _spill
            from openprogram.context.spill import spill_if_large, large_dir_for
            text = node.output
            # Cheap guard first: the overwhelming majority of nodes are
            # small, and this runs on every single append. Without it
            # every append would pay a filesystem round-trip.
            if (not isinstance(text, str)
                    or len(text) <= _spill.NODE_RENDER_CAP
                    or not _spill.SPILL_ENABLED):
                return
            stamp = spill_if_large(
                text,
                node_key=f"{node.seq:04d}-{node.id}",
                large_dir=large_dir_for(
                    str(self._session_dir(session_id) / "history")),
            )
            if stamp:
                meta = dict(node.metadata or {})
                meta["spilled"] = stamp
                node.metadata = meta
        except Exception as e:  # noqa: BLE001
            # Spilling is an optimisation; never block the write.
            _log.debug("spill skipped for %s: %s", session_id, e)

    def append_message(self, session_id: str, msg: dict[str, Any]) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        node = _msg_to_node(msg)
        # Idempotent — skip if id already known.
        if node.id in idx.nodes_by_id:
            return
        predecessor = _node_conv_predecessor(node)
        caller = _node_caller(node)
        _check_append_invariant(session_id, idx, node, predecessor, caller)
        seq = idx.append(node, predecessor=predecessor, caller=caller)
        self.spill_large_node(session_id, node)
        # Write the raw node file. Commit deferred to turn end.
        git.write_history(seq, node.role, node.id, node.to_dict())
        # Advance head only when the conversation actually grew: a
        # caller-less node chained onto the current tip (or the session's
        # first node). Any other insert — a compaction summary splicing
        # mid-chain, a side-branch write — leaves head alone; explicit
        # moves go through set_head (context/compaction.md §5).
        advanced = (not caller
                    and (idx.head_id is None or predecessor == idx.head_id))
        if advanced:
            idx.set_head(node.id)
        activity_at = time.time()
        idx.set_meta(updated_at=activity_at)
        # Persist activity time for every content append. When HEAD moved,
        # this also exposes the new active branch to the parent process.
        self._persist_meta(git, idx)
        # Registry: every appended message bumps updated_at（最新一次聊天
        # 时间，侧栏排序键）；user 消息顺带刷新 preview（debounced to disk）。
        fields: dict[str, Any] = {"updated_at": activity_at}
        if node.role == "user" and node.output:
            text = (node.output or "").strip().replace("\n", " ")
            fields["preview"] = (text[:77] + "…") if len(text) > 80 else text
        self._update_index_entry(session_id, **fields)
        self._schedule_index_flush()

    def append_messages(self, session_id: str, msgs: list[dict[str, Any]]) -> None:
        for m in msgs:
            self.append_message(session_id, m)

    @staticmethod
    def _rewrite_history_node(git: GitSession, node: Call) -> None:
        """Atomically replace one existing history node without syncing it."""
        role_letter = (node.role or "x")[0]
        path = git.path / "history" / (
            f"{node.seq:04d}-{role_letter}-{node.id}.json"
        )
        if path.exists():
            atomic_write_text(
                path,
                json.dumps(node.to_dict(), ensure_ascii=False, default=str),
            )

    def update_node(
        self, session_id: str, node_id: str, **fields: Any,
    ) -> None:
        """Update one current node and rewrite its history file once."""
        pair = self._open(session_id)
        if pair is None:
            return
        git, idx = pair
        node = idx.nodes_by_id.get(node_id)
        if node is None:
            return
        metadata = fields.pop("metadata", {})
        for key, value in fields.items():
            setattr(node, key, value)
        if "output" in fields:
            self.spill_large_node(session_id, node)
        if isinstance(metadata, dict):
            current = node.metadata if isinstance(node.metadata, dict) else {}
            node.metadata = {**current, **metadata}
        self._rewrite_history_node(git, node)

    def merge_node_metadata_batch(
        self,
        session_id: str,
        patches: dict[str, dict[str, Any]],
    ) -> None:
        """Merge one session's node metadata with one index open."""
        pair = self._open(session_id)
        if pair is None:
            return
        git, idx = pair
        for node_id, patch in patches.items():
            node = idx.nodes_by_id.get(node_id)
            if node is None:
                continue
            current = node.metadata if isinstance(node.metadata, dict) else {}
            node.metadata = {**current, **patch}
            self._rewrite_history_node(git, node)

    def merge_node_metadata(
        self, session_id: str, node_id: str, patch: dict[str, Any],
    ) -> None:
        """Merge metadata into one persisted node without touching session meta."""
        self.merge_node_metadata_batch(session_id, {node_id: patch})

    def get_messages(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        msgs = [
            _node_to_msg(n, session_id) for n in idx.all_nodes()
            if (n.metadata or {}).get("display") != "root"
            and not (n.metadata or {}).get("rewound")
            # ``context/*`` nodes record what the context pipeline sent
            # (dag/overview.md §7). They are machinery, not conversation, and
            # stay out of every chat/transcript view. ``get_nodes`` is the
            # raw view for the code that does want them.
            # ``context/summary`` is the exception: §8 makes it an ordinary
            # chain member carrying real conversation content (the recap
            # that stands in for the range it covers), so it is painted
            # and read like any other turn.
            and not _is_hidden_context_node(n)
        ]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs

    def get_branch(
        self,
        session_id: str,
        head_msg_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        head = head_msg_id or idx.head_id
        if not head or head not in idx.nodes_by_id:
            return []

        # Pure predecessor-edge walk (dag/overview.md). A
        # node without a predecessor must be a legal branch terminus
        # (spawn root / ROOT / session first node) — anything else is
        # broken data and raises. No caller/seq heuristics.
        def _edge(node):
            pred = _node_conv_predecessor(node)
            if pred:
                return pred
            meta = node.metadata or {}
            if _is_spawn_root(meta):
                return None          # spawn branch root — legal stop
            if meta.get("display") == "root":
                return None          # the ROOT node itself
            if node.role not in (ROLE_USER, ROLE_LLM):
                return None          # code node opening a run branch
            if _is_first_conv_node(idx, node):
                return None          # session first node — legal stop
            if meta.get("covers_ids") is not None:
                # Compaction summary covering from the very start of the
                # session: it inherits the first node's empty predecessor
                # and becomes the new chain terminus — the same exemption
                # the write invariant grants (§8 above).
                return None
            raise BrokenPredecessorChainError(session_id, node.id)

        chain = idx.get_branch(head, _edge)
        return [
            _node_to_msg(n, session_id) for n in chain
            if (n.metadata or {}).get("display") != "root"
            and not (n.metadata or {}).get("rewound")
        ]

    # Spawn primitive (dag/overview.md)

    def spawn_branch(
        self,
        session_id: str,
        caller_node_id: str,
        *,
        source: str,
        name: Optional[str] = None,
        node_id: Optional[str] = None,
        prompt: str = "",
        created_at: Optional[float] = None,
        metadata: Optional[dict] = None,
        register_head: bool = True,
    ) -> str:
        """Open a spawn branch: create the branch-root user node
        (``predecessor=None``, ``caller=caller_node_id``,
        ``metadata.source=source``) and return the branch-root id. The
        ONLY sanctioned way to open a spawn branch — call sites never
        hand-assemble the edge.

        ``register_head=False`` (same-session sub-agent spawns): the
        branch opens WITHOUT stealing the session head — the user's
        conversation stays the active branch while the agent runs
        (context/compaction.md §5, HEAD single-writer).
        """
        from .session_node_writer import SessionNodeWriter

        meta = dict(metadata or {})
        meta["source"] = source
        meta["spawn_branch_root"] = True
        meta.pop("predecessor", None)
        node = Call(
            id=node_id or uuid.uuid4().hex[:12],
            created_at=created_at or time.time(),
            role=ROLE_USER,
            output=prompt,
            caller=caller_node_id or "ROOT",
            predecessor=None,
            metadata=meta,
        )
        SessionNodeWriter(self, session_id).append(node)
        if register_head:
            # Register the branch head so mid-run loads resolve onto
            # the new branch (shim skips set_head for caller-tagged
            # nodes).
            self.set_head(session_id, node.id)
        if name:
            try:
                self.set_branch_name(session_id, node.id, name)
            except (OSError, ValueError, KeyError) as e:
                _log.warning("branch name %r NOT recorded for %s: %s",
                             name, node.id, e)
        return node.id

    # Head

    def set_head(self, session_id: str, head_id: Optional[str]) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        # A summary node is a stand-in, never a conversational tip: a
        # head on it makes the active branch [summary] alone and hides
        # the whole session (context/compaction.md §5). No caller has a
        # legitimate reason; refuse loudly instead of storing it.
        node = idx.nodes_by_id.get(head_id) if head_id else None
        if node is not None and (node.metadata or {}).get("covers_ids"):
            raise ValueError(
                f"set_head: {head_id!r} is a compaction summary — "
                "a stand-in cannot be the active branch tip"
            )
        idx.set_head(head_id)
        self._persist_meta(git, idx)

    def compare_and_set_head(
        self,
        session_id: str,
        expected_head_id: Optional[str],
        new_head_id: Optional[str],
        *,
        branch_update: Optional[dict[str, Any]] = None,
        meta_update: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Durable cross-process HEAD CAS, optionally activating a branch ref."""
        with self._session_lock(session_id):
            pair = self._open(session_id)
            if pair is None:
                return False
            git, idx = pair
            node = idx.nodes_by_id.get(new_head_id) if new_head_id else None
            if node is not None and (node.metadata or {}).get("covers_ids"):
                raise ValueError(
                    f"compare_and_set_head: {new_head_id!r} is a compaction summary"
                )
            with self._head_file_lock(git):
                try:
                    durable = git.read_meta()
                    if durable.get("head_id") != expected_head_id:
                        return False
                    updated = dict(durable)
                    updated["head_id"] = new_head_id
                    updated["head_version"] = int(
                        durable.get("head_version") or 0
                    ) + 1
                    if branch_update:
                        refs = dict(durable.get("branch_refs") or {})
                        source_id = branch_update.get("source_branch_id")
                        target_id = branch_update.get("target_branch_id")
                        if source_id:
                            source = dict(refs.get(source_id) or {})
                            source.setdefault("branch_id", source_id)
                            source.setdefault("head_id", expected_head_id)
                            source.setdefault("head_version", int(
                                durable.get("head_version") or 0
                            ))
                            source.setdefault("writer_epoch", int(
                                durable.get("writer_epoch") or 0
                            ))
                            refs[source_id] = source
                        if target_id and not branch_update.get("preserve_target"):
                            target = dict(refs.get(target_id) or {})
                            target.update({
                                "branch_id": target_id,
                                "head_id": new_head_id,
                                "parent_branch_id": source_id,
                                "head_version": updated["head_version"],
                                "writer_epoch": int(
                                    durable.get("writer_epoch") or 0
                                ) + 1,
                                "status": branch_update.get("target_status", "active"),
                            })
                            refs[target_id] = target
                        elif target_id and target_id in refs \
                                and branch_update.get("target_status"):
                            target = dict(refs[target_id])
                            target["status"] = branch_update["target_status"]
                            refs[target_id] = target
                        updated["branch_refs"] = refs
                        active_id = branch_update.get("active_branch_id")
                        if active_id:
                            updated["active_branch_id"] = active_id
                        updated["writer_epoch"] = int(
                            durable.get("writer_epoch") or 0
                        ) + 1
                    if meta_update:
                        updated.update(meta_update)
                    git.write_meta(updated)
                    with idx._persist_lock:
                        with idx._lock:
                            idx.head_id = new_head_id
                            idx.meta = updated
                    return True
                finally:
                    pass

    def message_exists(self, session_id: str, msg_id: str) -> bool:
        pair = self._open(session_id)
        if pair is None:
            return False
        _git, idx = pair
        return msg_id in idx.nodes_by_id

    # Branches

    def list_branches(self, session_id: str) -> list[dict[str, Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        # A branch tip is a conv node (no caller) with no conv-child.
        tips: list[dict[str, Any]] = []
        named = (idx.meta.get("branches") or {})
        # Identify the session's "main" tip — the leaf reached by
        # walking the earliest conv-root down its kids[0] primary
        # path. This matches the DAG lane-0 trunk exactly, so the
        # branch the user visually identifies as "the straight line
        # down the middle" gets the "main" label.
        roots = [
            n for n in idx.all_nodes()
            if (not n.caller or n.caller == "ROOT")
            and (_node_conv_predecessor(n) or "ROOT") == "ROOT"
            and (n.metadata or {}).get("display") != "root"
            and not _is_spawn_root(n.metadata or {})
        ]
        # Pure predecessor-edge walk (dag/overview.md): from
        # the earliest root, follow the primary (first-registered) conv
        # child until the chain ends. Spawn branch roots never appear
        # in ``children_by_predecessor`` (their predecessor is None),
        # so no spawn/task special-casing is needed.
        main_tip_id: Optional[str] = None
        if roots:
            cur = min(roots, key=lambda n: n.created_at).id
            seen: set[str] = set()
            while cur not in seen:
                seen.add(cur)
                kids = idx.children_by_predecessor.get(cur, [])
                if not kids:
                    break
                cur = kids[0]
            main_tip_id = cur

        merged = self.merged_heads(session_id)

        def _conv_child(kid_id: str) -> bool:
            """A child that CONTINUES the conversation. Execution-layer
            rows (attach pointers, runtime nodes, sub-call replies) and
            context machinery register under ``children_by_predecessor``
            too, but hanging one off a turn does not stop that turn
            being the branch tip — counting them dropped a branch from
            the panel the moment its head spawned a task."""
            ch = idx.nodes_by_id.get(kid_id)
            if ch is None:
                return False
            if ch.role not in (ROLE_USER, ROLE_LLM):
                return False
            md = ch.metadata or {}
            if md.get("display") in ("root", "runtime"):
                return False
            if md.get("function") == "attach":
                return False
            if str(ch.name or "").startswith("context/"):
                return False
            c = _node_caller(ch)
            if c and c != "ROOT":
                cn = idx.nodes_by_id.get(c)
                if cn is not None and cn.role not in (ROLE_USER, ROLE_LLM):
                    return False
            return True

        for node in idx.all_nodes():
            # Skip sub-call nodes — anything living INSIDE a function
            # run. Two shapes: a tool/code node with a real caller
            # (nested sub-call), and a user/llm node whose caller is a
            # code node (the LLM replies a run makes internally). The
            # old user/llm exemption assumed chat-world callers (a conv
            # predecessor or ROOT); with function runs it surfaced every
            # internal LLM leaf as a phantom "branch" in the panel.
            caller = _node_caller(node)
            if caller and caller != "ROOT":
                if node.role not in (ROLE_USER, ROLE_LLM):
                    continue
                caller_node = idx.nodes_by_id.get(caller)
                if caller_node is not None and caller_node.role not in (
                    ROLE_USER, ROLE_LLM,
                ):
                    continue
            if (node.metadata or {}).get("display") == "root":
                continue
            # ``context/*`` nodes (system_prompt, summary) are context
            # machinery, never a branch you can check out — dag/overview.md
            # §7 reserves the name and hides them from the chat views.
            if str(node.name or "").startswith("context/"):
                continue
            # Attach-pointer rows ride the assistant role but are
            # side-calls, not real branch tips. Old writes didn't
            # populate Call.caller so the ``node.caller`` check
            # above misses them — fall back to metadata.function.
            if (node.metadata or {}).get("function") == "attach":
                continue
            kids = idx.children_by_predecessor.get(node.id, [])
            if any(_conv_child(k) for k in kids):
                continue
            # Heads that a merge consumed don't surface as standalone
            # branches anymore — their content lives on the merge tip.
            if node.id in merged:
                continue
            label = named.get(node.id)
            name = label.get("name") if isinstance(label, dict) else label
            # No "main" special-case: the trunk tip is named exactly like
            # any other branch — its own name, or None (→ id short-hex in
            # the badge). The trunk identity is separate from the name.
            # See branch-naming.md 决策 3.
            tips.append({
                "head_msg_id": node.id,
                "name": name,
                "created_at": (label or {}).get("created_at") if isinstance(label, dict) else node.created_at,
                "updated_at": (label or {}).get("updated_at") if isinstance(label, dict) else node.created_at,
                "archived": bool(label.get("archived")) if isinstance(label, dict) else False,
            })
        # Main_tip may have children (the /task spawn it stopped at), so
        # the leaf-only loop above won't include it. Push it in by hand so
        # the right-rail Branches panel still lists the trunk you can
        # checkout to "go back" to — but with its own name (or None →
        # short-hex), no "main" special-case. See branch-naming.md 决策 3.
        if main_tip_id and not any(t["head_msg_id"] == main_tip_id for t in tips):
            main_node = idx.nodes_by_id.get(main_tip_id)
            if main_node:
                label = named.get(main_tip_id)
                name = (
                    label.get("name") if isinstance(label, dict)
                    else label
                )
                tips.append({
                    "head_msg_id": main_tip_id,
                    "name": name,
                    "created_at": main_node.created_at,
                    "updated_at": main_node.created_at,
                    "archived": bool(label.get("archived")) if isinstance(label, dict) else False,
                })
        # Compaction no longer clones the kept tail (§8): a summary node
        # splices into the chain and the tail keeps its own ids, so the
        # branch tips need no translation. A summary node that ends up a
        # tip is still machinery, not a checkout target.
        tips = [t for t in tips
                if not str(t["head_msg_id"]).startswith("summary_")]
        tips.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
        return tips

    def set_branch_name(
        self,
        session_id: str,
        head_msg_id: str,
        name: str,
        **fields: Any,
    ) -> None:
        """Set a branch's name, merging (not replacing) its meta entry.

        ``**fields`` writes auto-naming state alongside the name
        (``auto_named`` / ``name_locked`` / ``name_gen_count`` / ``turns``;
        see docs/design/runtime/branch-naming.md). Unspecified existing
        fields are preserved — callers that only touch the name must not
        wipe the lock, the counters, or the archive flag."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair

        def _mutate(entry: dict) -> None:
            now = time.time()
            entry["name"] = name
            entry.setdefault("created_at", now)
            entry["updated_at"] = now
            entry.update(fields)

        idx.update_branch_entry(head_msg_id, _mutate)
        self._persist_meta(git, idx)

    def get_branch_meta(self, session_id: str, head_msg_id: str) -> dict[str, Any]:
        """Return a branch's full meta entry (name + auto-naming state),
        or ``{}`` if the branch has no entry. Used by the auto-namer to
        re-read the lock before writing back (see branch-naming.md
        "优先级与锁")."""
        pair = self._open(session_id)
        if pair is None:
            return {}
        _git, idx = pair
        return dict((idx.meta.get("branches") or {}).get(head_msg_id) or {})

    def set_branch_meta(
        self, session_id: str, head_msg_id: str, **fields: Any,
    ) -> None:
        """Merge ``fields`` into a branch's meta entry without touching
        its name. Used for lifecycle facts that ride the same entry as
        the name (``archived`` / ``archived_at`` / ``archived_reason``
        — see agent-collaboration.md, archiving)."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair

        def _mutate(entry: dict) -> None:
            now = time.time()
            entry.update(fields)
            entry.setdefault("created_at", now)
            entry["updated_at"] = now

        idx.update_branch_entry(head_msg_id, _mutate)
        self._persist_meta(git, idx)

    def bump_branch_turns(self, session_id: str, head_msg_id: str) -> int:
        """Increment a branch's per-branch turn counter and return the
        new value. Used by finalize_turn to decide whether to trigger
        Stage-2 auto-rename (counter, not a message count — see
        branch-naming.md 第四节)."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return 0
        git, idx = pair

        def _mutate(entry: dict) -> None:
            entry["turns"] = int(entry.get("turns", 0)) + 1

        merged = idx.update_branch_entry(head_msg_id, _mutate)
        self._persist_meta(git, idx)
        return int(merged.get("turns", 0))

    def delete_branch_name(self, session_id: str, head_msg_id: str) -> None:
        pair = self._open(session_id)
        if pair is None:
            return
        git, idx = pair
        branches = dict(idx.meta.get("branches") or {})
        if branches.pop(head_msg_id, None) is None:
            return
        idx.set_meta(branches=branches)
        self._persist_meta(git, idx)

    def delete_branch_tail(self, session_id: str, head_msg_id: str) -> int:
        pair = self._open(session_id)
        if pair is None:
            return 0
        git, idx = pair
        if head_msg_id not in idx.nodes_by_id:
            return 0
        # Fork point the doomed subtree hangs off — the head fallback
        # if the session head turns out to live inside the subtree.
        root_pred = _node_conv_predecessor(idx.nodes_by_id[head_msg_id])
        # Collect head + descendants (both conv-children and sub-calls).
        to_delete: list[str] = [head_msg_id]
        seen: set[str] = {head_msg_id}
        stack: list[str] = [head_msg_id]
        while stack:
            cur = stack.pop()
            for cid in idx.children_by_predecessor.get(cur, []):
                if cid not in seen:
                    seen.add(cid)
                    to_delete.append(cid)
                    stack.append(cid)
            for cid in idx.children_by_caller.get(cur, []):
                if cid not in seen:
                    seen.add(cid)
                    to_delete.append(cid)
                    stack.append(cid)

        # Drop from index + remove history files. ID set kept so the
        # rest of the index stays consistent.
        with idx._lock:
            for nid in to_delete:
                node = idx.nodes_by_id.pop(nid, None)
                if node is None:
                    continue
                # Remove from sorted list (linear scan; tiny lists in practice)
                idx.nodes_by_seq = [n for n in idx.nodes_by_seq if n.id != nid]
                # Free the seq so append can reuse it (the collision guard
                # in SessionMemoryIndex.append reads this set).
                idx._taken_seqs.discard(node.seq)
                # Detach from children indices
                for parent, kids in list(idx.children_by_predecessor.items()):
                    if nid in kids:
                        kids.remove(nid)
                        if not kids:
                            del idx.children_by_predecessor[parent]
                for parent, kids in list(idx.children_by_caller.items()):
                    if nid in kids:
                        kids.remove(nid)
                        if not kids:
                            del idx.children_by_caller[parent]
                # File removal
                for fpath in (git.path / "history").glob(f"*-{nid}.json"):
                    try:
                        fpath.unlink()
                    except OSError:
                        pass
        # Drop named branches for the deleted heads
        branches = dict(idx.meta.get("branches") or {})
        for nid in to_delete:
            branches.pop(nid, None)
        idx.set_meta(branches=branches)
        # A head inside the deleted subtree must not survive the
        # deletion — a dangling head renders the session empty. Fall
        # back to the fork point the subtree hung off (None = the tail
        # was the whole session). Callers that pick a smarter new head
        # (the ws handler moves to another named leaf) overwrite this.
        if idx.head_id in seen:
            idx.set_head(root_pred)
        self._persist_meta(git, idx)
        return len(to_delete)

    def mark_merged(self, session_id: str, head_ids: list[str]) -> None:
        """Record that these head ids have been merged into another
        branch — the Branches panel hides them after this.

        The DAG nodes stay intact (a checkout still works) but the
        head no longer surfaces as a standalone branch tip.
        """
        pair = self._open(session_id)
        if pair is None:
            return
        git, idx = pair
        with idx._lock:
            cur = list(idx.meta.get("merged_heads") or [])
            changed = False
            for h in head_ids:
                if not h:
                    continue
                h = h.strip()
                if h and h not in cur:
                    cur.append(h)
                    changed = True
            if changed:
                idx.meta["merged_heads"] = cur
        if changed:
            self._persist_meta(git, idx)

    def merged_heads(self, session_id: str) -> set[str]:
        """Head ids a merge absorbed into another branch.

        ``list_branches`` hides these (their content is reachable from
        the surviving branch), and addressing uses the same set to keep
        a merged head resolving to ITSELF instead of snapping onto
        whichever live branch swallowed it.
        """
        pair = self._open(session_id)
        if pair is None:
            return set()
        _git, idx = pair
        merged = set(idx.meta.get("merged_heads") or [])
        # Fallback: auto-detect merged peers by scanning recent
        # ContextCommits for multi-parent commits (= merge commits).
        # Their non-primary parents resolve to head_node_ids that
        # got consumed by the merge. This catches the case where
        # ``mark_merged`` was bypassed by an early-return in
        # ``process_merge_turn`` so the panel still cleans up.
        try:
            from openprogram.context.commit.store import (
                list_commits, load_commit,
            )
            for _c in list_commits(self, session_id, limit=50) or []:
                pids = list(_c.commit_parents or [])
                if len(pids) > 1:
                    for _pid in pids[1:]:
                        try:
                            _peer = load_commit(self, _pid, session_id=session_id)
                        except Exception as e:  # noqa: BLE001 — one bad peer must not
                            # drop the rest of the merge scan.
                            _log.debug("merge peer %s unreadable: %s", _pid, e)
                            _peer = None
                        if _peer is not None and _peer.head_node_id:
                            merged.add(_peer.head_node_id)
        except Exception as e:  # noqa: BLE001 — commit subsystem is optional here
            _log.debug("merge-head scan skipped for %s: %s", session_id, e)
        return merged

    def list_archived_branches(self, session_id: str) -> list[dict[str, Any]]:
        """Every archived branch of the session, most recently touched
        first, in the same row shape as ``list_branches``.

        Archiving and merging are orthogonal: a merge absorbs a branch
        into another one and drops it from the live tip list, archiving
        records that the agent on it is finished. This view reads the
        ``archived`` flag straight off the branch entries, so a branch
        that was both merged and archived is still listed.
        """
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        rows: list[dict[str, Any]] = []
        # Snapshot: a concurrent turn can add a branch entry mid-scan.
        for head, entry in list((idx.meta.get("branches") or {}).items()):
            if not isinstance(entry, dict) or not entry.get("archived"):
                continue
            if head not in idx.nodes_by_id:
                continue
            rows.append({
                "head_msg_id": head,
                "name": entry.get("name"),
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
                "archived": True,
            })
        rows.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
        return rows

    def drop_message(self, session_id: str, node_id: str) -> bool:
        """Remove a single node by id — no descendant walk.

        Used to retire attach-pointer rows that have been consumed by
        a merge: the pointer is a side-channel reference, not part of
        the conv chain, so dropping it doesn't orphan anything. Caller
        is responsible for ``commit_turn`` if a git commit should
        record the deletion.
        """
        pair = self._open(session_id)
        if pair is None:
            return False
        git, idx = pair
        if node_id not in idx.nodes_by_id:
            return False
        with idx._lock:
            _removed = idx.nodes_by_id.pop(node_id, None)
            idx.nodes_by_seq = [n for n in idx.nodes_by_seq if n.id != node_id]
            if _removed is not None:
                idx._taken_seqs.discard(_removed.seq)
            for parent, kids in list(idx.children_by_predecessor.items()):
                if node_id in kids:
                    kids.remove(node_id)
                    if not kids:
                        del idx.children_by_predecessor[parent]
            for parent, kids in list(idx.children_by_caller.items()):
                if node_id in kids:
                    kids.remove(node_id)
                    if not kids:
                        del idx.children_by_caller[parent]
            for fpath in (git.path / "history").glob(f"*-{node_id}.json"):
                try:
                    fpath.unlink()
                except OSError:
                    pass
        branches = dict(idx.meta.get("branches") or {})
        if node_id in branches:
            branches.pop(node_id, None)
            idx.set_meta(branches=branches)
        self._persist_meta(git, idx)
        return True

    # Token stats / search / misc — these are derived, keep
    # implementations consistent with old DagSessionDB by delegating
    # to the existing helpers where they're pure functions.

    def get_branch_token_stats(
        self,
        session_id: str,
        head_msg_id: Optional[str] = None,
        *,
        head_id: Optional[str] = None,
        model: Any = None,
    ) -> dict[str, Any]:
        head = head_id or head_msg_id
        chain = self.get_branch(session_id, head) if head else self.get_messages(session_id)
        model_id = getattr(model, "id", None) or (model if isinstance(model, str) else None)

        input_total = output_total = cache_read_total = cache_write_total = 0
        messages_counted = 0
        last_input_tokens = 0
        last_model = None
        for m in chain:
            if m.get("role") != "assistant":
                continue
            if model_id is not None and m.get("token_model") != model_id:
                continue
            i = int(m.get("input_tokens") or 0)
            o = int(m.get("output_tokens") or 0)
            input_total += i
            output_total += o
            cache_read_total += int(m.get("cache_read_tokens") or 0)
            cache_write_total += int(m.get("cache_write_tokens") or 0)
            messages_counted += 1
            if i:
                last_input_tokens = i
            if m.get("token_model"):
                last_model = m["token_model"]
        current_tokens = last_input_tokens + output_total // max(messages_counted, 1)
        denom = cache_read_total + input_total
        cache_hit_rate = (cache_read_total / denom) if denom else 0.0
        return {
            "input_tokens": input_total, "output_tokens": output_total,
            "cache_read_tokens": cache_read_total, "cache_write_tokens": cache_write_total,
            "cache_read_total": cache_read_total, "cache_hit_rate": cache_hit_rate,
            "messages_counted": messages_counted, "current_tokens": current_tokens,
            "context_window": 0, "pct_used": 0.0,
            "model": last_model or model_id,
        }

    def session_commits(self, session_id: str, *, limit: int = 100) -> list:
        """The session repo's per-turn git commits, newest first.

        Each successful turn is one commit (``commit_turn``), so this is
        the entity layer's time axis — the turn boundaries with their sha
        + timestamp + message. Returns ``GitSession.CommitInfo`` records.

        Exposed because the per-turn commits were being *written* but had
        no reader (``GitSession.log`` was only ever called by itself): the
        memory provenance layer and the UI timeline both want to walk a
        session's turns. Empty list if the session doesn't exist yet.
        """
        pair = self._open(session_id)
        if pair is None:
            return []
        git, _idx = pair
        return git.log(limit=limit)

    def get_nodes(self, session_id: str) -> list[Call]:
        """Raw Call objects for a session, sorted by seq.

        Lower-level than ``get_messages`` (which returns msg-dict shape)
        — used by code that builds a Graph view (e.g. exec-DAG tree).
        """
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        return idx.all_nodes()

    def latest_user_text(self, session_id: str) -> Optional[str]:
        pair = self._open(session_id)
        if pair is None:
            return None
        _git, idx = pair
        for n in reversed(idx.all_nodes()):
            if n.is_user():
                return n.output
        return None

    def sessions_with_binding(self, channel: str, account_id: Optional[str]) -> list[str]:
        out: list[str] = []
        for sess in self.list_sessions(limit=10**9, include_archived=True):
            extra = sess.get("extra_meta") or {}
            if extra.get("channel") != channel:
                continue
            if account_id is not None and extra.get("account_id") != account_id:
                continue
            out.append(sess["id"])
        return out

    def search_messages(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from .search import search_messages as _do_search
        return _do_search(
            self, query,
            session_id=session_id, agent_id=agent_id, limit=limit,
        )

    def get_descendants(self, session_id: str, msg_id: str) -> list[dict[str, Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        if msg_id not in idx.nodes_by_id:
            return []
        # Old semantics: descendants follow caller only (sub-calls of
        # this node, not retry siblings).
        out = idx.descendants(msg_id, follow_caller=True)
        # The descendants helper crawls predecessor by default; here we
        # want caller-only. Implement inline to mirror old behavior.
        result: list[Call] = []
        stack = list(idx.children_by_caller.get(msg_id, []))
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            node = idx.nodes_by_id.get(cur)
            if node:
                result.append(node)
                stack.extend(idx.children_by_caller.get(cur, []))
        return [_node_to_msg(n, session_id) for n in result]

    def get_deepest_leaf(self, session_id: str, msg_id: Optional[str] = None) -> Optional[str]:
        pair = self._open(session_id)
        if pair is None:
            return None
        _git, idx = pair
        # Old semantics: longest message-tree chain via the predecessor field.
        children = idx.children_by_predecessor
        roots = [msg_id] if msg_id else [
            n.id for n in idx.all_nodes()
            if not _node_conv_predecessor(n)
        ]
        deepest_id: Optional[str] = None
        deepest_depth = -1
        for root in roots:
            if root not in idx.nodes_by_id:
                continue
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack:
                cur, depth = stack.pop()
                kids = children.get(cur, [])
                if not kids:
                    if depth > deepest_depth:
                        deepest_depth = depth
                        deepest_id = cur
                else:
                    for c in kids:
                        stack.append((c, depth + 1))
        return deepest_id

    def count_recent_nodes(self, since: float) -> int:
        total = 0
        for sess in self.list_sessions(limit=10**9, include_archived=True):
            pair = self._open(sess["id"])
            if not pair:
                continue
            _git, idx = pair
            for n in idx.all_nodes():
                if (n.created_at or 0) >= since:
                    total += 1
        return total

    def close(self) -> None:
        return None


# Singleton


_default_lock = threading.Lock()
_default_store: Optional[SessionStore] = None


def default_store() -> SessionStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = SessionStore()
    return _default_store
