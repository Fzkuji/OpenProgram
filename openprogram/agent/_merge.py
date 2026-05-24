"""Merge N peer sessions into one target reply.

In the peer-session model, ``merge`` is the symmetric counterpart of
``attach``:

  * ``attach`` makes one session's turn visible inside another's DAG
    via a pointer node (``run_sub_agent_turn``).
  * ``merge`` aggregates N peer sessions' results into one new turn
    on a designated target session, recording the ancestry as a
    multi-parent ContextCommit.

There's no parent / child between the sessions themselves — those
labels are just node-level relationships ("this attach node lives
on session X", "this merge node had these N parents"). The sessions
are equal peers.

Pipeline:

  1. For each input session: load its latest ContextCommit (so the
     ``parent_ids`` we write can point at a real commit, not a stub),
     and pull its final assistant text from the session's HEAD chain
     (one short string we can drop into the merge prompt).
  2. Build a prompt with ``<session label="...">…</session>`` blocks
     plus the user's merge instruction.
  3. Run ``process_user_turn`` on the target session
     (``history_override=[]`` — the prompt is self-contained).
  4. Save a fresh ContextCommit on the target with
     ``parent_ids = [target's prior commit id, *peer commit ids]``.

The merge result lands on the target session as a regular assistant
turn; the multi-parent ContextCommit records the lineage so the UI
timeline can show where the merge drew from.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MergeTurnResult:
    target_session_id: str = ""
    target_assistant_id: Optional[str] = None
    commit_id: Optional[str] = None
    parent_ids: list[str] = field(default_factory=list)
    final_text: str = ""
    failed: bool = False
    error: Optional[str] = None


def _peer_final_text(
    store, sid: str, head_id: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Pull the most recent assistant content + resolved head id from a
    peer branch. ``head_id=None`` falls back to the session's current
    head — i.e. "whatever the user is looking at". A specific head_id
    lets the caller pick a branch tip (sibling head, historical fork
    point, etc.) within the same session.

    Returns (text, head_id). Empty text + None head means the branch
    has no assistant turns or the session is unknown."""
    pair = store._open(sid)
    if not pair:
        return "", None
    _git, idx = pair
    head = head_id or idx.head_id
    if not head or head not in idx.nodes_by_id:
        return "", head
    visited: set[str] = set()
    cur = head
    while cur and cur not in visited:
        visited.add(cur)
        node = idx.nodes_by_id.get(cur)
        if node is None:
            break
        if (node.role or "") in ("assistant", "llm") and (node.output or "").strip():
            return str(node.output), head
        meta = node.metadata or {}
        cur = meta.get("parent_id") or node.called_by or None
    return "", head


def _peer_latest_commit_id(store, sid: str, head_id: Optional[str]) -> Optional[str]:
    """Best-available ContextCommit id for a peer session — load the
    branch-specific commit if we have a head, otherwise the
    session-global latest. Returns None when the session has no
    committed context yet."""
    if not head_id:
        return None
    try:
        from openprogram.context.commit.store import load_commit_for_head
        c = load_commit_for_head(store, sid, head_id)
        if c is not None:
            return c.id
    except Exception:
        pass
    try:
        from openprogram.context.commit.store import load_latest_commit
        c = load_latest_commit(store, sid)
        if c is not None:
            return c.id
    except Exception:
        pass
    return None


def _build_prompt(peers: list[dict], message: str) -> str:
    parts = [
        "Multiple peer agents produced results in parallel.",
        "Their individual final replies are below — consolidate them",
        "into a single coherent answer.",
        "",
    ]
    for p in peers:
        label = p.get("label") or p.get("session_id") or "peer"
        text = (p.get("text") or "").strip() or "(no output)"
        parts.append(f'<session label="{label}">')
        parts.append(text)
        parts.append("</session>")
        parts.append("")
    if message and message.strip():
        parts.append("Merge instruction:")
        parts.append(message.strip())
    return "\n".join(parts)


def _commit_id() -> str:
    return f"commit_{secrets.token_hex(8)}"


def process_merge_turn(
    target_session_id: str,
    sub_sessions: Optional[list] = None,
    message: str = "",
    agent_id: str = "main",
    *,
    peers: Optional[list[dict]] = None,
) -> MergeTurnResult:
    """Aggregate N branches onto ``target_session_id``.

    A "branch" here is a ``(session_id, head_id)`` pair. The two input
    forms (``peers`` taking dicts, ``sub_sessions`` taking session ids)
    coexist for back-compat — internally we normalize to one list of
    ``{session_id, head_id}`` records. ``head_id=None`` falls back to
    that session's current head.

    Same-session and cross-session merges go through the same code:
    pass two entries with the same ``session_id`` and different
    ``head_id``s to merge two siblings in one DAG; pass entries from
    different sessions to merge peer agents.
    """
    from openprogram.agent.session_db import default_db
    from openprogram.context.commit.store import save_commit, load_commit_for_head
    from openprogram.context.commit.types import (
        ContextCommit, CURRENT_RULES_VERSION,
    )

    store = default_db()
    if store._open(target_session_id) is None:
        return MergeTurnResult(
            target_session_id=target_session_id,
            failed=True,
            error=f"target session {target_session_id!r} not found",
        )

    raw_peers: list[dict] = []
    if peers:
        for p in peers:
            if not isinstance(p, dict):
                continue
            sid = (p.get("session_id") or "").strip()
            if not sid:
                continue
            raw_peers.append({
                "session_id": sid,
                "head_id": (p.get("head_id") or None),
            })
    if sub_sessions:
        for sid in sub_sessions:
            sid_s = (str(sid) if sid is not None else "").strip()
            if not sid_s:
                continue
            raw_peers.append({"session_id": sid_s, "head_id": None})

    if not raw_peers:
        return MergeTurnResult(
            target_session_id=target_session_id,
            failed=True,
            error="no peer branches provided",
        )

    resolved: list[dict] = []
    missing: list[str] = []
    for p in raw_peers:
        sid = p["session_id"]
        head_id_in = p["head_id"]
        text, head_id = _peer_final_text(store, sid, head_id_in)
        if not text and head_id is None:
            missing.append(f"{sid}:{head_id_in or 'HEAD'}")
            continue
        resolved.append({
            "session_id": sid,
            "text": text,
            "head_id": head_id,
            "label": _label_for(store, sid, head_id),
            "commit_id": _peer_latest_commit_id(store, sid, head_id),
        })

    if not resolved:
        return MergeTurnResult(
            target_session_id=target_session_id,
            failed=True,
            error=(
                "no peer branches yielded content; "
                f"missing={missing!r}"
            ),
        )
    peers = resolved

    merge_prompt = _build_prompt(peers, message)
    from openprogram.agent.dispatcher import TurnRequest, process_user_turn

    req = TurnRequest(
        session_id=target_session_id,
        user_text=merge_prompt,
        agent_id=agent_id,
        source="merge_turn",
        history_override=[],
    )
    try:
        turn = process_user_turn(req)
    except Exception as e:  # noqa: BLE001
        return MergeTurnResult(
            target_session_id=target_session_id,
            failed=True,
            error=f"{type(e).__name__}: {e}",
        )

    if turn.failed:
        return MergeTurnResult(
            target_session_id=target_session_id,
            target_assistant_id=turn.assistant_msg_id,
            final_text=turn.final_text or "",
            failed=True,
            error=turn.error,
        )

    parents: list[str] = []
    prev = load_commit_for_head(store, target_session_id, turn.assistant_msg_id)
    if prev is not None and prev.id:
        parents.append(prev.id)
    for p in peers:
        if p.get("commit_id"):
            parents.append(p["commit_id"])

    commit = ContextCommit(
        id=_commit_id(),
        session_id=target_session_id,
        parent_id=parents[0] if parents else None,
        parent_ids=parents,
        created_at=time.time(),
        head_node_id=turn.assistant_msg_id,
        rules_version=CURRENT_RULES_VERSION,
        total_tokens=len(turn.final_text or "") // 4,
        items=[],
        summary="merge: " + ", ".join(p["label"] for p in peers),
    )
    try:
        save_commit(store, commit)
    except Exception as e:  # noqa: BLE001
        return MergeTurnResult(
            target_session_id=target_session_id,
            target_assistant_id=turn.assistant_msg_id,
            commit_id=None,
            parent_ids=parents,
            final_text=turn.final_text or "",
            failed=True,
            error=f"save_commit failed: {type(e).__name__}: {e}",
        )

    try:
        store.commit_turn(
            target_session_id,
            f"merge: {' + '.join(p['label'] for p in peers)}",
        )
    except Exception:
        pass

    return MergeTurnResult(
        target_session_id=target_session_id,
        target_assistant_id=turn.assistant_msg_id,
        commit_id=commit.id,
        parent_ids=parents,
        final_text=turn.final_text or "",
        failed=False,
    )


def _label_for(store, sid: str, head_id: Optional[str] = None) -> str:
    """Human-readable handle for a peer branch in the merge prompt.

    Same-session branches are disambiguated by suffixing a short head
    id (e.g. ``Main@d80a74``) so a merge prompt that bundles two
    sibling forks doesn't list them with the same label."""
    try:
        sess = store.get_session(sid) or {}
        base = ""
        for k in ("label", "title"):
            v = sess.get(k)
            if isinstance(v, str) and v.strip():
                base = v.strip()[:32]
                break
        if not base:
            base = sid
        if head_id and sess.get("head_id") != head_id:
            return f"{base}@{head_id[:6]}"
        return base
    except Exception:
        return sid
