"""DagSession — multi-session lifecycle for the flat-DAG chat stack.

A *session* is one conversation: a row in ``sessions`` plus zero-or-more
rows in ``nodes`` (the conversation as a flat DAG). The whole thing
lives in one SQLite file. The session manager (``DagSessionManager``)
handles create / load / list / delete across many sessions in that one
file.

A single ``DagSession`` object bundles:

  - a ``Graph`` (in-memory view of the conversation)
  - a ``GraphStore`` (SQLite reader / writer for one session's nodes)
  - a ``DagRuntime`` (wraps a provider call + auto-appends nodes)
  - ``SessionMeta`` (title / model / agent_id / source / extra)

The data model (UserMessage / ModelCall / FunctionCall + predecessor +
reads) is preserved exactly. SQLite is just the storage substrate.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from openprogram.context.nodes import Graph
from openprogram.context.storage import (
    GraphStore,
    init_db,
    list_session_rows,
    read_session_row,
    delete_session as _delete_session_row,
)
from openprogram.context.runtime import DagRuntime


# ── Metadata ────────────────────────────────────────────────────────


@dataclass
class SessionMeta:
    """Per-session metadata: corresponds to one row in the ``sessions`` table."""

    id: str
    title: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    model: str = ""
    agent_id: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict) -> "SessionMeta":
        import json
        return cls(
            id=row["id"],
            title=row.get("title") or "",
            created_at=row.get("created_at") or time.time(),
            updated_at=row.get("updated_at") or time.time(),
            model=row.get("model") or "",
            agent_id=row.get("agent_id") or "",
            source=row.get("source") or "",
            extra=json.loads(row.get("extra_json") or "{}"),
        )


# ── Single session ──────────────────────────────────────────────────


class DagSession:
    """One persisted conversation. Owns a Graph, a GraphStore, and a DagRuntime."""

    def __init__(
        self,
        *,
        meta: SessionMeta,
        graph: Graph,
        store: GraphStore,
        runtime: DagRuntime,
    ):
        self.meta = meta
        self.graph = graph
        self.store = store
        self.runtime = runtime

    @property
    def id(self) -> str:
        return self.meta.id

    # ── Lifecycle ────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        db_path: str | Path,
        *,
        provider_call: Callable,
        session_id: Optional[str] = None,
        model: str = "",
        title: str = "",
        agent_id: str = "",
        source: str = "",
        extra: Optional[dict] = None,
    ) -> "DagSession":
        """Create a brand-new session row + return a DagSession for it."""
        init_db(db_path)
        sid = session_id or _new_session_id()
        store = GraphStore(db_path, sid)
        if store.session_exists():
            raise ValueError(f"Session {sid!r} already exists in {db_path}")
        store.create_session_row(
            title=title,
            model=model,
            agent_id=agent_id,
            source=source,
            extra=extra,
        )
        graph = store.load()  # empty graph initially
        runtime = DagRuntime(provider_call, graph=graph, store=store, default_model=model)

        meta = SessionMeta(
            id=sid,
            title=title,
            model=model,
            agent_id=agent_id,
            source=source,
            extra=extra or {},
        )
        return cls(meta=meta, graph=graph, store=store, runtime=runtime)

    @classmethod
    def load(
        cls,
        db_path: str | Path,
        session_id: str,
        *,
        provider_call: Callable,
    ) -> "DagSession":
        """Reopen an existing session."""
        row = read_session_row(db_path, session_id)
        if row is None:
            raise FileNotFoundError(f"No session {session_id!r} in {db_path}")
        meta = SessionMeta.from_row(row)
        store = GraphStore(db_path, session_id)
        graph = store.load()
        runtime = DagRuntime(provider_call, graph=graph, store=store, default_model=meta.model)
        return cls(meta=meta, graph=graph, store=store, runtime=runtime)

    # ── Mutation ─────────────────────────────────────────────────

    def touch(self, **meta_updates: Any) -> None:
        """Update meta fields (title, model, agent_id, source, extra) and persist."""
        valid = {"title", "model", "agent_id", "source", "extra"}
        for k, v in meta_updates.items():
            if k in valid:
                setattr(self.meta, k, v)
        self.meta.updated_at = time.time()
        self.store.update_session_row(
            **{k: v for k, v in meta_updates.items() if k in valid}
        )

    # ── Search ───────────────────────────────────────────────────

    def search(self, query: str, *, limit: int = 50) -> list[str]:
        """Full-text search within this session. Returns node ids."""
        return self.store.search(query, limit=limit)


# ── Multi-session manager ───────────────────────────────────────────


class DagSessionManager:
    """List / create / load / delete sessions in one SQLite DB."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        init_db(self.db_path)

    # ── Listing ──────────────────────────────────────────────────

    def list(self) -> list[SessionMeta]:
        """Return all sessions, newest first by ``updated_at``."""
        return [SessionMeta.from_row(r) for r in list_session_rows(self.db_path)]

    def exists(self, session_id: str) -> bool:
        return read_session_row(self.db_path, session_id) is not None

    # ── Create / load / delete ───────────────────────────────────

    def create(
        self,
        *,
        provider_call: Callable,
        session_id: Optional[str] = None,
        **meta_fields: Any,
    ) -> DagSession:
        return DagSession.create(
            self.db_path,
            provider_call=provider_call,
            session_id=session_id,
            **meta_fields,
        )

    def load(self, session_id: str, *, provider_call: Callable) -> DagSession:
        return DagSession.load(
            self.db_path,
            session_id,
            provider_call=provider_call,
        )

    def delete(self, session_id: str) -> bool:
        return _delete_session_row(self.db_path, session_id)

    def rename(self, session_id: str, new_title: str) -> None:
        """Set the title. Session id is not changed."""
        if not self.exists(session_id):
            raise FileNotFoundError(session_id)
        GraphStore(self.db_path, session_id).update_session_row(title=new_title)


# ── Helpers ─────────────────────────────────────────────────────────


def _new_session_id() -> str:
    """Generate a human-friendly session id: ``20260514-103045-abc12345``."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{ts}-{uuid.uuid4().hex[:8]}"


__all__ = [
    "SessionMeta",
    "DagSession",
    "DagSessionManager",
]
