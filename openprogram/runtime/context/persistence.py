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

# Summary nodes are llm-role nodes (a model wrote them) and carry this name
# so renderers/UI can recognise them without an id prefix sniff.
SUMMARY_NODE_NAME = "context/summary"

# What a summary's text starts with. One written as user input
# (``render_role: "user"``) says what it is to the model reading it, as
# Codex and Pi do; an older one, rendered as the assistant's, kept the bracket.
SUMMARY_PREFIX = "The earlier part of this conversation was compacted into this summary:\n"
LEGACY_SUMMARY_PREFIX = "[Previous conversation summary]\n"


def summary_content(summary_text: str, render_role: str | None) -> str:
    """The stored text of a summary node rendered as ``render_role``."""
    prefix = SUMMARY_PREFIX if render_role == "user" else LEGACY_SUMMARY_PREFIX
    return prefix + summary_text


def covered_chain_ids(covered: list[dict]) -> list[str]:
    """The chain-node ids a new summary replaces, expressed in real turns.

    ``covered`` is the head slice of the RENDERED history
    (context/compaction.md §4): when the session was compacted before,
    its first element is the previous summary. Coverage is always stated
    over the underlying conversation, so a covered summary contributes
    its own ``covers_ids`` — the new segment extends the old one — and
    is itself never named (the newer summary takes its place).
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


def rendered_history(db, session_id: str,
                     head_id: str | None = None) -> list[dict]:
    """The message-dict view of what the model reads on the active
    branch (or the branch ending at ``head_id``): the summary in effect
    first (when its segment applies), then the kept turns
    (context/compaction.md §4 step 1).

    Which summary is in effect is ``nodes.active_summary_for_chain``, the
    same rule ``render_context`` applies, so this view and the rendered
    request never disagree about it.

    This is what compaction must consume as its input. Feeding the raw
    ``get_branch`` walk re-summarises turns the previous summary
    already ate and produces a second summary with duplicate coverage.
    """
    branch = db.get_branch(session_id, head_id) or []
    get_nodes = getattr(db, "get_nodes", None)
    if get_nodes is None:
        return branch
    from openprogram.runtime.context.nodes import active_summary_for_chain, summary_covers_ids
    try:
        row = getattr(db, "get_session", lambda _sid: None)(session_id)
        retracted = (row if isinstance(row, dict) else {}).get("rewind_hidden_nodes") or ()
        summary = active_summary_for_chain(get_nodes(session_id) or [],
                                           (m.get("id") for m in branch), retracted)
        rows = db.get_messages_by_id(session_id, [summary.id]) if summary is not None else []
    except Exception:
        return branch
    if not rows:
        return branch
    covered = set(summary_covers_ids(summary) or ())
    return [rows[0]] + [m for m in branch if m.get("id") not in covered]


class Persister:
    """Writes summary nodes to SessionDB."""

    def insert_summary_node(self,
                            session_id: str,
                            *,
                            summary_text: str,
                            cut_idx: int,
                            history: list[dict],
                            facts: Optional[dict] = None,
                            context_window: int = 0,
                            restored_state: str = "",
                            restored_files: Optional[dict] = None,
                            ) -> Optional[str]:
        """Insert the summary node covering ``history[:cut_idx]``.
        Returns the new summary id (or None on failure).

        ``facts`` are what the compaction knows when it writes: its trigger,
        model, occupancy before and after, covered count and time. They go
        into the node in the same write, so the node is complete the
        moment it exists. So do the deferred tools the covered turns had
        loaded (``carried_tools.carried_tool_names``), cut to what fits
        ``context_window``.

        The summary takes the predecessor of ``history[0]`` (the first
        covered node) and records ``metadata.covers_ids``. The kept tail
        is left completely untouched — its rows keep their ids, their
        predecessors and their timestamps — and HEAD stays where it is:
        the append rule ignores mid-chain splices.

        Crash safety: the summary insert is one write. Before it the
        previous view stands; after it the node carries everything its row
        and the transcript marker show.
        """
        if not summary_text or cut_idx <= 0 or cut_idx >= len(history):
            return None

        from openprogram.runtime.context.session_db import default_db

        db = default_db()

        covered = history[:cut_idx]
        first = covered[0]
        covers_ids = covered_chain_ids(covered)
        if not covers_ids:
            return None

        from openprogram.runtime.context.carried_tools import CARRIED_TOOL_NAMES, carried_tool_names
        get_nodes = getattr(db, "get_nodes", None)
        try:
            carried = carried_tool_names(get_nodes(session_id) or [] if get_nodes else [],
                                         covers_ids, context_window)
        except Exception:
            _log.warning("could not read the tools the covered turns loaded", exc_info=True)
            carried = []

        summary_id = "summary_" + uuid.uuid4().hex[:10]
        # Order the summary immediately before the first node it covers
        # so chronological reads place the recap where the range began.
        first_ts = float(first.get("timestamp") or time.time())

        summary_row = {
            "id": summary_id,
            "role": "llm",
            # _msg_to_node's llm branch reads Call.name off ``token_model``.
            "token_model": SUMMARY_NODE_NAME,
            "content": summary_content(summary_text, (facts or {}).get("render_role")),
            # Splice in at the position of the range being replaced.
            "predecessor": first.get("predecessor") or None,
            "timestamp": first_ts - 1e-6,
            "type": "compactionSummary",
            "source": "compaction",
            "extra": {
                **(facts or {}),
                "compaction": True,
                "covers_ids": covers_ids,
                CARRIED_TOOL_NAMES: carried,
                "predecessor": first.get("predecessor") or None,
                **({"restored_state": restored_state} if restored_state else {}),
                **({"restored_files": restored_files} if restored_files else {}),
            },
        }

        from openprogram.state.memory.policy import node_policy, from_snapshot, restrict_history
        policies = [node_policy(row) for row in covered if node_policy(row)]
        if policies:
            summary_row["memory_policy"] = restrict_history(from_snapshot(policies[-1]), covered).snapshot()
        try:
            db.append_message(session_id, summary_row)
        except Exception:
            return None
        return summary_id


default_persister = Persister()
