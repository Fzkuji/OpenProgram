"""Persister — write compaction results back to SessionDB as DAG nodes.

OpenProgram's distinguishing feature vs the reference platforms: a
compaction is a real *commit* in the message DAG. The summary is an
ordinary ``role=llm`` node that stands in for the range it replaces::

    parent ─┬─ covered[0] ── ... ── covered[-1] ── kept_tail ── HEAD
            └─ summary_node   (metadata.covers_ids = covered ids)

The summary node's ``predecessor`` is the predecessor of the FIRST node
it covers, so it sits exactly where the covered segment began.
``metadata.covers_ids`` records the exact chain nodes it replaces — ids,
not a seq interval, because seqs of sibling branches interleave
(context/compaction.md §2). ``render_context`` substitutes the segment
with the summary on any chain that contains it (§3); the graph folds the
same ids behind the capsule (dag/rendering.md §9).

Nothing is cloned, nothing is deleted, and HEAD does not move: the
append rule only advances head on chain extension, and a summary is a
mid-chain splice. The pre-compaction view stays reachable by ignoring
the summary.

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


def covered_chain_ids(covered: list[dict]) -> list[str]:
    """The chain-node ids a new summary replaces, expressed in real turns.

    ``covered`` is the head slice of the RENDERED history
    (context/compaction.md §4): when the session was compacted before,
    its first element is the previous summary. Coverage is always stated
    over the underlying conversation, so a covered summary contributes
    its own ``covers_ids`` — the new segment extends the old one — and
    is itself never named (it retires via ``_last_summary_id``).
    """
    out: list[str] = []
    for m in covered:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not mid:
            continue
        # The msg adapter flattens ``extra`` into the top-level dict on
        # the way out; accept both shapes. ``extra`` can survive as a
        # raw JSON string on some rows — only dicts are readable.
        nested = m.get("extra")
        if not isinstance(nested, dict):
            nested = m.get("metadata")
        if not isinstance(nested, dict):
            nested = {}
        prev = m.get("covers_ids") or nested.get("covers_ids")
        if isinstance(prev, (list, tuple)) and prev:
            out.extend(str(x) for x in prev if str(x) not in out)
        elif str(mid) not in out:
            out.append(str(mid))
    return out


def rendered_history(db, session_id: str) -> list[dict]:
    """The message-dict view of what the model reads on the active
    branch: active summary first (when its segment applies), then the
    kept turns (context/compaction.md §4 step 1).

    This is what compaction must consume as its input. Feeding the raw
    ``get_branch`` walk re-summarises turns the previous summary
    already ate and produces a second summary with duplicate coverage.
    """
    branch = db.get_branch(session_id) or []
    try:
        msgs = db.get_messages(session_id) or []
    except Exception:
        return branch
    summaries = [m for m in msgs if isinstance(m, dict)
                 and m.get("covers_ids")]
    if not summaries:
        return branch
    active = summaries[-1]          # append order — the newest wins
    seg = [str(x) for x in active["covers_ids"]]
    branch_ids = {m.get("id") for m in branch}
    if not seg or not all(s in branch_ids for s in seg):
        return branch
    segset = set(seg)
    return [active] + [m for m in branch if m.get("id") not in segset]


class Persister:
    """Writes summary nodes to SessionDB."""

    def insert_summary_node(self,
                            session_id: str,
                            *,
                            summary_text: str,
                            cut_idx: int,
                            history: list[dict],
                            ) -> Optional[str]:
        """Insert the summary node covering ``history[:cut_idx]``.
        Returns the new summary id (or None on failure).

        The summary takes the predecessor of ``history[0]`` (the first
        covered node) and records ``metadata.covers_ids``. The kept tail
        is left completely untouched — its rows keep their ids, their
        predecessors and their timestamps — and HEAD stays where it is:
        the append rule ignores mid-chain splices.

        Crash safety: the summary insert is its own transaction. If we
        crash before ``_last_summary_id`` moves, rendering simply hasn't
        got a new summary to honour yet — the user sees the previous
        view, nothing breaks.
        """
        if not summary_text or cut_idx <= 0 or cut_idx >= len(history):
            return None

        from openprogram.agent.session_db import default_db

        db = default_db()

        covered = history[:cut_idx]
        first = covered[0]
        covers_ids = covered_chain_ids(covered)
        if not covers_ids:
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
                "covers_ids": covers_ids,
                "predecessor": first.get("predecessor") or None,
            },
        }

        try:
            db.append_message(session_id, summary_row)
        except Exception:
            return None
        return summary_id


default_persister = Persister()
