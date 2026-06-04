"""Provenance read-layer — the seam memory maps from.

The entity layer (git-backed session DAG + per-turn commits + the
project working-dir repo) is the ground truth that memory distills into
its virtual layer. This module is the thin, **LLM-free** read surface
that exposes the entity layer as *coordinates* memory can map onto:

  * ``iter_nodes_since`` — incremental node cursor (only what's new
    since the last ingest pass);
  * ``node_provenance``  — a node → ``Provenance`` coordinate;
  * ``session_commits``  — the session repo's per-turn commit history
    (delegates to ``SessionStore.session_commits``);
  * ``project_commits``  — the project working-dir repo's
    agent-attributed commit history.

Why it lives on the store side, not inside ``memory/``: memory is
pluggable (``MemoryProvider``). Keeping "how to read the git DAG +
provenance" here means any memory backend (builtin / mem0 / a graph DB)
maps the *same* stable surface instead of each re-deriving the git
layout. The entity layer owns "expose mappable coordinates"; memory owns
"map them into whatever shape it wants". See
docs/design/memory/entity-session-cache.md §5 and memory-v2.md §3.1
(bi-temporal provenance).

Nothing here calls an LLM, so it is cheap and unit-testable in isolation
— the Phase-2 extractor (the expensive LLM pass that turns these
coordinates into timeline events / graph edges) sits *above* this layer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from openprogram.context.nodes import Call

if TYPE_CHECKING:
    from openprogram.store.session.session_store import SessionStore


@dataclass(frozen=True)
class Provenance:
    """A coordinate pointing a virtual memory back at its entity-layer source.

    bi-temporal (memory-v2 §3.1):
      * ``event_time``      — when it happened (the node's ``created_at``);
      * ``ingestion_time``  — when memory recorded it (caller-stamped).

    ``node_ids`` is a tuple (not a list) so the whole record is hashable
    / frozen — a Provenance can be a dict key or live in a set during
    dedup. ``commit`` is the session-git turn commit the node belongs to,
    when known (optional — it's resolved lazily, a node exists before its
    turn is committed).
    """
    project_id: str
    session_id: str
    node_ids: tuple[str, ...]
    commit: Optional[str] = None
    event_time: float = 0.0
    ingestion_time: float = 0.0


def session_project_id(store: "SessionStore", session_id: str) -> str:
    """The project this session belongs to, read straight from the
    session repo's ``meta.json`` (``""`` if unknown / no such session)."""
    pair = store._open(session_id)
    if pair is None:
        return ""
    _git, idx = pair
    return (idx.meta or {}).get("project_id") or ""


def iter_nodes_since(
    store: "SessionStore",
    session_id: str,
    *,
    after_seq: int = -1,
) -> list[Call]:
    """Nodes with ``seq > after_seq``, in seq order — the incremental
    cursor for memory ingest.

    Pass the highest ``seq`` seen in the previous pass to get only what's
    new; the default ``after_seq=-1`` returns every node (seq starts at
    0). This is the right granularity for "distill the new turns since I
    last looked" without re-reading the whole session each time.

    Returns raw ``Call`` nodes (not rendered msg dicts) so the extractor
    sees the full DAG: tool/code nodes with their tool name + input +
    output, and the ``reads`` edges that record what influenced a
    decision — exactly the structure ``get_branch``'s rendered text
    flattens away.
    """
    return [n for n in store.get_nodes(session_id) if (n.seq or 0) > after_seq]


def node_provenance(
    store: "SessionStore",
    session_id: str,
    node: Call,
    *,
    commit: Optional[str] = None,
    ingestion_time: Optional[float] = None,
) -> Provenance:
    """Build the entity-layer coordinate for a single node.

    ``ingestion_time`` defaults to now (when memory is recording this);
    pass an explicit value to keep a whole ingest batch on one timestamp.
    ``commit`` is optional — the caller passes the session-git turn sha
    if it has resolved it (see ``session_commits``).
    """
    return Provenance(
        project_id=session_project_id(store, session_id),
        session_id=session_id,
        node_ids=(node.id,),
        commit=commit,
        event_time=float(node.created_at or 0),
        ingestion_time=float(ingestion_time if ingestion_time is not None else time.time()),
    )


def session_commits(store: "SessionStore", session_id: str, *, limit: int = 100) -> list:
    """The session repo's per-turn git commits, newest first
    (delegates to ``SessionStore.session_commits``)."""
    return store.session_commits(session_id, limit=limit)


def project_commits(project_id: str, *, limit: int = 100) -> list[dict]:
    """Agent-attributed commits in the project's working-dir repo, newest
    first — the other half of the entity layer ("what the agent changed
    on disk"), which ``memory/`` has never read.

    Returns ``[]`` for the default project (a logical label with no repo)
    or any project whose path isn't a git repo yet.
    """
    from openprogram.store.project import project_store as _p
    proj = _p.get_project(project_id)
    if proj is None or not proj.path:
        return []
    return _p.ProjectGit(proj.path).log(limit=limit)
