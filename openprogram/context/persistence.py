"""Persister — write compaction results back to SessionDB as DAG nodes.

OpenProgram's distinguishing feature vs the reference platforms: a
compaction is a real *commit* in the message DAG. The summary is an
ordinary ``role=llm`` node that splices into the conversation chain at
the position of the range it replaces::

    parent ── summary_node ── kept_tail[0] ── kept_tail[1] ── ... ── HEAD
                 │
                 └ metadata.covers = [first_seq, last_seq]

``covers`` is the whole trick. The summary node's ``predecessor`` is the
predecessor of the FIRST node it covers, so it sits exactly where the
covered range began. Head then moves onto the chain that continues past
the covered range. Renderers walking the chain skip any node whose seq
falls inside an encountered ``covers`` interval — the covered nodes are
still on the chain, still readable, just elided from the prompt.

Nothing is cloned and nothing is deleted. The pre-compaction view is
still reachable by ignoring ``covers`` (or by checking out the branch
that runs through the covered nodes), which is what makes "did the
summary really capture what I said" answerable.

This module owns just the DB-write side of that contract. Decisions
about *when* to compact and *what* the summary text contains live in
the engine and summarizer respectively.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

_log = logging.getLogger(__name__)

# Summary nodes render as an llm turn (they are a model-written recap),
# and carry this name so renderers/UI can recognise them without an id
# prefix sniff.
SUMMARY_NODE_NAME = "context/summary"


def _seq_index(db, session_id: str) -> dict:
    """``node id → seq`` for one session, off the stored graph.

    The message-dict boundary drops ``seq``, but ``covers`` is expressed
    in seq (it must be — ids don't order), so read it back from the
    store's own node index.
    """
    try:
        from openprogram.store.session.graphstore_shim import GraphStoreShim
        graph = GraphStoreShim(db, session_id).load()
        return {n.id: n.seq for n in graph.nodes.values()}
    except Exception:
        return {}


class Persister:
    """Writes summary nodes to SessionDB."""

    def insert_summary_node(self,
                            session_id: str,
                            *,
                            summary_text: str,
                            cut_idx: int,
                            history: list[dict],
                            ) -> Optional[str]:
        """Insert the summary node covering ``history[:cut_idx]`` and
        advance head. Returns the new summary id (or None on failure).

        The summary takes the predecessor of ``history[0]`` (the first
        covered node) and records ``metadata.covers = [first_seq,
        last_seq]``. The kept tail is left completely untouched — its
        rows keep their ids, their predecessors and their timestamps.
        Head stays where it is unless it pointed inside the covered
        range, because the tail already continues past the summary.

        Crash safety: the summary insert is its own transaction. If we
        crash before head moves, ``get_branch`` follows the same chain
        as before and the render layer simply hasn't got a summary to
        honour yet — the user sees full history, nothing breaks.
        """
        if not summary_text or cut_idx <= 0 or cut_idx >= len(history):
            return None

        from openprogram.agent.session_db import default_db

        db = default_db()

        covered = history[:cut_idx]
        first, last = covered[0], covered[-1]
        # History dicts don't carry seq (the message-dict boundary drops
        # it), so resolve the covered range off the stored graph by id.
        seq_by_id = _seq_index(db, session_id)
        first_seq = seq_by_id.get(first.get("id"))
        last_seq = seq_by_id.get(last.get("id"))
        if first_seq is None or last_seq is None:
            return None

        summary_id = "summary_" + uuid.uuid4().hex[:10]
        # Order the summary immediately before the first node it covers
        # so chronological reads place the recap where the range began.
        first_ts = float(first.get("timestamp") or time.time())

        summary_row = {
            "id": summary_id,
            "role": "llm",
            # _msg_to_node's llm branch reads Call.name off ``token_model``.
            "token_model": SUMMARY_NODE_NAME,
            "content": f"[Previous conversation summary]\n{summary_text}",
            # Splice in at the position of the range being replaced.
            "predecessor": first.get("predecessor") or None,
            "timestamp": first_ts - 1e-6,
            "type": "compactionSummary",
            "source": "compaction",
            "extra": {
                "compaction": True,
                "covers": [int(first_seq), int(last_seq)],
                "predecessor": first.get("predecessor") or None,
            },
        }

        # Snapshot head BEFORE the insert: append_message auto-advances
        # head onto any caller-less node it appends, and the summary is
        # a mid-chain splice, not a new tip. Reading head after the
        # append sees the summary itself and the restore below would
        # never fire — that exact sequence detached the whole kept tail
        # (active branch = [summary] alone).
        prev_head = (db.get_session(session_id) or {}).get("head_id")

        try:
            db.append_message(session_id, summary_row)
        except Exception:
            return None

        # Head only belongs on the summary when it sat inside the
        # covered range; otherwise restore it — the existing tail
        # already continues past the summary.
        try:
            covered_ids = {m.get("id") for m in covered}
            if prev_head and prev_head not in covered_ids:
                db.set_head(session_id, prev_head)
        except Exception:
            # A head left on the summary hides the kept tail from the
            # active branch; loud enough to diagnose, not fatal.
            _log.warning(
                "failed to restore head after summary %s for session %s",
                summary_id, session_id, exc_info=True,
            )

        return summary_id


default_persister = Persister()
