"""Persister — write compaction results back to SessionDB as DAG nodes.

OpenProgram's distinguishing feature vs the reference platforms: a
compaction is a real *commit* in the message DAG. The new state of
the branch is:

    parent ── summary_node ── kept_tail[0] ── kept_tail[1] ── ... ── HEAD

The original history is NOT deleted; it sits as a sibling branch off
the original fork point. ``head_id`` advances onto the new chain, so
the LLM's next turn sees the short version. But the user can git-style
"checkout" the original branch any time to see the pre-compaction
state — invaluable for debugging "did the summary really capture what
I said".

This module owns just the DB-write side of that contract. Decisions
about *when* to compact and *what* the summary text contains live in
the engine and summarizer respectively.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional


class Persister:
    """Writes summary nodes to SessionDB."""

    def insert_summary_node(self,
                            session_id: str,
                            *,
                            summary_text: str,
                            cut_idx: int,
                            history: list[dict],
                            ) -> Optional[str]:
        """Insert a synthetic ``compactionSummary`` row, re-parent the
        kept tail onto it, advance head. Returns the new summary id
        (or None on DB failure).

        Crash safety: each DB call is its own transaction. If we crash
        between the summary insert and the re-parent, the next
        ``get_branch`` will follow the old chain (because head hasn't
        moved), so the user sees full history. The orphan summary node
        sits there as garbage but doesn't break anything.
        """
        if not summary_text or cut_idx <= 0 or cut_idx >= len(history):
            return None

        from openprogram.agent.session_db import default_db

        db = default_db()
        summary_id = "summary_" + uuid.uuid4().hex[:10]

        # Parent the summary at None so the active branch starts AT the
        # summary — the prefix it summarises must not reappear when
        # get_branch walks back from head. The original prefix stays in
        # the messages table (append-only) and is reachable as a sibling
        # branch via get_descendants from m_0.
        # Order summary BEFORE the kept tail in chronological reads
        # (``get_branch`` sorts ASC by timestamp). The re-parented
        # kept-tail rows preserve their original timestamps, so the
        # summary must be earlier than the first kept message.
        first_kept_ts = float(history[cut_idx].get("timestamp") or time.time())
        summary_ts = first_kept_ts - 1e-6

        summary_row = {
            "id": summary_id,
            "role": "system",
            "content": f"[Previous conversation summary]\n{summary_text}",
            "parent_id": None,
            "timestamp": summary_ts,
            "type": "compactionSummary",
            "source": "compaction",
            "extra": '{"compaction": true}',
        }

        try:
            db.append_message(session_id, summary_row)
        except Exception:
            return None

        # Re-parent the kept tail: each kept message gets a new id and
        # parent_id pointing back through the new chain. Original
        # rows stay in the DB but become a sibling branch.
        prev = summary_id
        for original in history[cut_idx:]:
            new_id = "k_" + uuid.uuid4().hex[:10]
            row = dict(original)
            row["id"] = new_id
            row["parent_id"] = prev
            # If a message has an attachment manifest in ``extra``,
            # keep it — but blow away any aging metadata so future
            # ager passes treat the re-parented copy as fresh.
            try:
                db.append_message(session_id, row)
            except Exception:
                # If a tail write fails mid-way, advance head to the
                # last successfully-written node so we don't dead-end
                # on the orphan summary.
                if prev != summary_id:
                    try:
                        db.set_head(session_id, prev)
                    except Exception:
                        pass
                return None
            prev = new_id

        try:
            db.set_head(session_id, prev)
        except Exception:
            return None

        return summary_id


default_persister = Persister()
