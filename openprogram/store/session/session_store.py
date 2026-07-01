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
from collections import OrderedDict
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

from .git_session import GitSession
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
    except Exception:
        return "default"


# Edge resolvers
# A node has two distinct parent edges:
#   * caller       ─ Call.caller  (the LLM/ROOT that invoked a sub-call)
#   * predecessor  ─ metadata.predecessor  (the conversation-chain parent)
# Keep these as standalone functions so the index doesn't know about
# message-dict field layout.


def _node_conv_predecessor(payload_or_call) -> Optional[str]:
    """Return the conv-chain predecessor of a node (or None)."""
    if isinstance(payload_or_call, Call):
        return (payload_or_call.metadata or {}).get("predecessor") or None
    meta = (payload_or_call.get("metadata") or {})
    return meta.get("predecessor") or payload_or_call.get("predecessor") or None


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

    ``db_path`` attr is kept for compatibility (e.g. webui code that
    inspects ``default_db().db_path`` to derive other paths). It maps
    to the root directory holding per-session repos, not a single
    SQLite file.
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
        self._load_index()
        atexit.register(self._flush_index)

    # Compatibility shim. Old code does ``db.db_path / "subdir"`` for
    # ancillary files — point that at the root so existing usage doesn't
    # break before we audit each call site.
    @property
    def db_path(self) -> Path:
        return self.root_path

    # Location index (per-project session placement)

    def _locations_path(self) -> Path:
        return self.root_path / "locations.json"

    def _load_locations(self) -> dict[str, str]:
        p = self.root_path / "locations.json"
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_locations(self) -> None:
        tmp = self._locations_path().with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(self._locations, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._locations_path())
        except OSError:
            pass

    def _record_location(self, session_id: str, repo_dir: Path) -> None:
        """Persist that ``session_id``'s repo lives at ``repo_dir`` (an
        absolute path outside the home root). Idempotent."""
        with self._lock:
            self._locations[session_id] = str(repo_dir)
            self._save_locations()

    def _forget_location(self, session_id: str) -> None:
        """删会话时移除位置映射（配对 _record_location）。"""
        with self._lock:
            if self._locations.pop(session_id, None) is not None:
                self._save_locations()

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
                data = json.loads(p.read_text(encoding="utf-8"))
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
                meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
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
                except Exception:
                    pass
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
            try:
                shutil.rmtree(self._session_dir(sid), ignore_errors=True)
            except Exception:
                pass
        # Capacity: trim oldest archived sessions beyond the limit.
        dirty = bool(to_delete)
        if len(self._index) > self._CAPACITY_LIMIT:
            archived = [(s, e) for s, e in self._index.items() if e.get("archived")]
            archived.sort(key=lambda x: x[1].get("updated_at") or 0)
            excess = len(self._index) - self._CAPACITY_LIMIT
            for sid, _ in archived[:excess]:
                self._index.pop(sid, None)
                try:
                    import shutil
                    shutil.rmtree(self._session_dir(sid), ignore_errors=True)
                except Exception:
                    pass
                dirty = True
        if dirty:
            self._save_index()

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
        p = self._index_path()
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(self._index, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(p)
        except OSError:
            pass
        self._index_dirty = False

    def _schedule_index_flush(self) -> None:
        with self._lock:
            self._index_dirty = True
            if self._index_timer is not None:
                return
            self._index_timer = threading.Timer(5.0, self._do_deferred_flush)
            self._index_timer.daemon = True
            self._index_timer.start()

    def _do_deferred_flush(self) -> None:
        with self._lock:
            self._index_timer = None
            if self._index_dirty:
                self._save_index()

    def _flush_index(self) -> None:
        with self._lock:
            if self._index_timer is not None:
                self._index_timer.cancel()
                self._index_timer = None
            if self._index_dirty:
                self._save_index()

    def _update_index_entry(self, session_id: str, **fields: Any) -> None:
        entry = self._index.get(session_id)
        if entry is None:
            entry = {"id": session_id, "status": "idle", "pinned": False,
                     "archived": False, "unread": False}
            self._index[session_id] = entry
        for k, v in fields.items():
            if k in self._INDEX_FIELDS or k == "preview":
                entry[k] = v
        entry["updated_at"] = time.time()

    # Internals

    def _session_dir(self, session_id: str) -> Path:
        """Where ``session_id``'s git repo lives.

        Project-bound sessions live inside their project at
        ``<project>/.openprogram/sessions/<id>/`` (recorded in the
        location index). Everything else — ad-hoc chats, all
        pre-existing sessions — resolves to the home root
        ``<state>/sessions/<id>/``.
        """
        loc = self._locations.get(session_id)
        if loc:
            return Path(loc)
        return self.root_path / session_id

    def _open(self, session_id: str, *, create_if_missing: bool = False) -> Optional[tuple[GitSession, SessionMemoryIndex]]:
        """Return (git, idx). Loads from disk on first access. None if
        session doesn't exist and ``create_if_missing`` is False."""
        with self._lock:
            cached = self._sessions.get(session_id)
            if cached:
                # Mark as most-recently-used so the LRU eviction below
                # never drops a session that's actively being read.
                self._sessions.move_to_end(session_id)
                return cached
            sdir = self._session_dir(session_id)
            if not sdir.exists() and not create_if_missing:
                return None
            git = GitSession(sdir)
            idx = SessionMemoryIndex()
            if git.exists():
                idx.rebuild_from_paths(
                    git.list_history(),
                    git.read_meta(),
                    _node_conv_predecessor,
                    _node_caller,
                )
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

    def _persist_meta(self, git: GitSession, idx: SessionMemoryIndex) -> None:
        """Sync the in-memory meta back to ``meta.json``. Called whenever
        title / head_id / extra / branches change."""
        meta = dict(idx.meta)
        meta["head_id"] = idx.head_id
        meta["updated_at"] = time.time()
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
        # docs/design/memory/memory-v2.md §2):
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
            from openprogram.store import project_store as _projects
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
        except Exception:
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
                from openprogram.store import project_store as _projects
                _projects.bind_session(session_id, project_id)
            except Exception:
                pass

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
        with self._lock:
            pair = self._sessions.pop(session_id, None)
            if pair:
                pair[0].destroy()
            else:
                GitSession(self._session_dir(session_id)).destroy()
            self._index.pop(session_id, None)
        self._save_index()
        # 位置映射（配对 _record_location）。
        self._forget_location(session_id)
        # 从项目反向索引解绑（配对 bind_session），避免 session_ids 只增不减。
        try:
            from openprogram.store import project_store as _projects
            _projects.unbind_session(session_id)
        except Exception:
            pass

    def list_sessions(
        self,
        *,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        source: Optional[str] = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        rows = list(self._index.values())
        if agent_id is not None:
            rows = [r for r in rows if r.get("agent_id") == agent_id]
        if source is not None:
            rows = [r for r in rows if r.get("source") == source]
        for k, v in filters.items():
            if v is not None:
                rows = [r for r in rows if r.get(k) == v]
        rows.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
        return rows[offset:offset + limit]

    def count_sessions(
        self,
        *,
        agent_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        return len(self.list_sessions(agent_id=agent_id, source=source, limit=10**9))

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
        with self._lock:
            self._sessions.pop(session_id, None)

    # Message append / read

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
        seq = idx.append(node, predecessor=predecessor, caller=caller)
        # Write the raw node file. Commit deferred to turn end.
        git.write_history(seq, node.role, node.id, node.to_dict())
        # Advance head for conversation nodes (no caller). Matches old
        # GraphStore.append behavior where caller-tagged nodes don't
        # bump last_node_id.
        if not caller:
            idx.set_head(node.id)
        idx.set_meta(updated_at=time.time())
        # Update registry preview on user messages (debounced to disk).
        if node.role == "user" and node.output:
            text = (node.output or "").strip().replace("\n", " ")
            preview = (text[:77] + "…") if len(text) > 80 else text
            self._update_index_entry(session_id, preview=preview)
            self._schedule_index_flush()

    def append_messages(self, session_id: str, msgs: list[dict[str, Any]]) -> None:
        for m in msgs:
            self.append_message(session_id, m)

    def get_messages(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        msgs = [
            _node_to_msg(n, session_id) for n in idx.all_nodes()
            if (n.metadata or {}).get("display") != "root"
            and not (n.metadata or {}).get("rewound")
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

        # Edge resolver: prefer conv parent, fall back to caller.
        # When a ROOT-parented node is reached, find the previous
        # ROOT-level node by seq — but only if that previous node
        # doesn't already have conv children (which would mean it's
        # the root of a different branch / fork).
        _root_nodes_by_seq = [
            n for n in idx.nodes_by_seq
            if (_node_caller(n) or "") in ("", "ROOT")
            and not _node_conv_predecessor(n)
            and (n.metadata or {}).get("display") != "root"
        ]

        def _edge(node):
            pred = _node_conv_predecessor(node)
            if pred:
                return pred
            caller = _node_caller(node)
            if caller and caller != "ROOT":
                return caller
            # ROOT-parented node with no conv predecessor.
            # Find the previous ROOT-level node by seq.
            node_seq = node.seq if hasattr(node, "seq") else -1
            prev = None
            for rn in _root_nodes_by_seq:
                rn_seq = rn.seq if hasattr(rn, "seq") else -1
                if rn_seq < node_seq:
                    prev = rn
                else:
                    break
            if prev is not None:
                # Only connect if the previous ROOT node does NOT have
                # conv children — if it does, it's the root of another
                # branch (fork), and we should not cross into it.
                has_conv_children = bool(
                    idx.children_by_predecessor.get(prev.id)
                )
                if not has_conv_children:
                    return prev.id
            # Fork branch root or first node — stop walking.
            return None

        chain = idx.get_branch(head, _edge)
        return [
            _node_to_msg(n, session_id) for n in chain
            if (n.metadata or {}).get("display") != "root"
            and not (n.metadata or {}).get("rewound")
        ]

    # Head

    def set_head(self, session_id: str, head_id: Optional[str]) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        idx.set_head(head_id)
        idx.set_meta(updated_at=time.time())
        self._persist_meta(git, idx)

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
            if not n.caller and not _node_conv_predecessor(n)
            and (n.metadata or {}).get("display") != "root"
        ]
        main_tip_id: Optional[str] = None
        if roots:
            cur = min(roots, key=lambda n: n.created_at).id
            hops = 0
            while hops < 1000:
                hops += 1
                kids = idx.children_by_predecessor.get(cur, [])
                if not kids:
                    main_tip_id = cur
                    break
                # Pick the primary child for main-lane walk: skip kids
                # spawned by /task (``source=agent_spawn``) — those
                # belong to a sub-agent's own lane, not main. Without
                # this filter the walk dives into the sub-agent
                # branch (sub-agent's user msg lands at seq < the
                # followup turn, so it claims kids[0] by accident)
                # and main_tip ends up on the merged sub-branch.
                primary: Optional[str] = None
                for kid_id in kids:
                    kid = idx.nodes_by_id.get(kid_id)
                    if not kid:
                        continue
                    if (kid.metadata or {}).get("source") == "agent_spawn":
                        continue
                    primary = kid_id
                    break
                if primary is None:
                    main_tip_id = cur
                    break
                # Main trunk stops at /task spawn forks — the spawned
                # turn (and the sub-agent's reply) belong to a new
                # branch, same as git `checkout -b`. lane.py applies
                # the same rule visually so the two stay in sync.
                nxt = idx.nodes_by_id.get(primary)
                if nxt and (nxt.metadata or {}).get("function") == "task":
                    main_tip_id = cur
                    break
                cur = primary

        merged = set((idx.meta.get("merged_heads") or []))
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
                        except Exception:
                            _peer = None
                        if _peer is not None and _peer.head_node_id:
                            merged.add(_peer.head_node_id)
        except Exception:
            pass
        for node in idx.all_nodes():
            # Skip sub-call nodes (tool/code with a real caller).
            # Don't skip user/assistant nodes — their caller is
            # a conv predecessor or ROOT, not a sub-call caller.
            caller = _node_caller(node)
            if caller and caller != "ROOT" and node.role not in (ROLE_USER, ROLE_LLM):
                continue
            if (node.metadata or {}).get("display") == "root":
                continue
            # Attach-pointer rows ride the assistant role but are
            # side-calls, not real branch tips. Old writes didn't
            # populate Call.caller so the ``node.caller`` check
            # above misses them — fall back to metadata.function.
            if (node.metadata or {}).get("function") == "attach":
                continue
            kids = idx.children_by_predecessor.get(node.id, [])
            if kids:
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
                })
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
        wipe the lock or counters."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        branches = dict(idx.meta.get("branches") or {})
        now = time.time()
        existing = dict(branches.get(head_msg_id) or {})
        existing.update({
            "name": name,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        })
        existing.update(fields)
        branches[head_msg_id] = existing
        idx.set_meta(branches=branches)
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

    def bump_branch_turns(self, session_id: str, head_msg_id: str) -> int:
        """Increment a branch's per-branch turn counter and return the
        new value. Used by finalize_turn to decide whether to trigger
        Stage-2 auto-rename (counter, not a message count — see
        branch-naming.md 第四节)."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return 0
        git, idx = pair
        branches = dict(idx.meta.get("branches") or {})
        entry = dict(branches.get(head_msg_id) or {})
        entry["turns"] = int(entry.get("turns", 0)) + 1
        branches[head_msg_id] = entry
        idx.set_meta(branches=branches)
        self._persist_meta(git, idx)
        return entry["turns"]

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
            idx.set_meta(merged_heads=cur)
            self._persist_meta(git, idx)

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
            idx.nodes_by_id.pop(node_id, None)
            idx.nodes_by_seq = [n for n in idx.nodes_by_seq if n.id != node_id]
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
        for sess in self.list_sessions(limit=10**9):
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
        # Old semantics: longest message-tree chain via metadata.predecessor.
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
        for sess in self.list_sessions(limit=10**9):
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
