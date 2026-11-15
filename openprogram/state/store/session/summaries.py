"""Which compaction summary a chain reads.

A summary node (``metadata.covers_ids``) stands in for the turns it covers
on any chain that holds them all. The rule lives with the store so the
graph builder and the context render (``runtime.context.nodes``) share it.
"""
from __future__ import annotations

from typing import Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.state.store.session.nodes import Call, Graph


def summary_covers_ids(node: "Call") -> Optional[list[str]]:
    """The chain-node ids a summary replaces, or None for non-summaries.

    Written by the compaction persister as ``metadata.covers_ids`` —
    ids, not a seq interval, because seqs of sibling branches interleave
    (context/compaction.md §2).
    """
    raw = (node.metadata or {}).get("covers_ids")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    return [str(x) for x in raw]


def active_summary_for_chain(graph: "Graph | Iterable[Call]",
                             chain_ids: Iterable[str],
                             retracted: Optional[Iterable[str]] = None) -> Optional["Call"]:
    """The summary a chain reads in place of the turns it covers, or None.

    The one rule every reader of "which summary is in effect" calls:
    ``render_context``, ``persistence.rendered_history``, the compaction
    input built from both, and the graph builder's fold of each branch.

    Each chain has its own (context/compaction.md, "Which summary"): of the
    summaries whose covered turns all lie on this chain, the one whose
    coverage reaches furthest along it. ``seq`` only breaks a tie. A summary
    written on another branch never applies here, so switching branches
    renders each branch with its own summary and writes nothing (Pi's "last
    compaction on the path").

    A rewind retracts in place (``retracted``, else the graph's own
    ``retracted`` set): a retracted message is not on the chain, and a
    retracted summary does not exist. So rewinding into a summary's range
    falls back to an earlier summary that still lies wholly on the chain,
    or to the raw turns when there is none.

    A covered id that names the summary itself or no node in the graph is
    ignored, so a summary is judged by the turns it really replaces.

    ``graph`` is a ``Graph`` or any iterable of its nodes.
    """
    nodes = list(graph.nodes.values() if hasattr(graph, "nodes") else graph)
    known = {node.id for node in nodes}
    gone = set(retracted if retracted is not None else getattr(graph, "retracted", ()) or ())
    position = {nid: index for index, nid in enumerate(
        nid for nid in chain_ids if nid not in gone)}
    best = None
    best_key = None
    for node in nodes:
        if node.id in gone or node.id in position:
            continue
        covers = summary_covers_ids(node)
        if covers is not None:
            covers = [cid for cid in covers if cid != node.id and cid in known]
        if not covers or not all(cid in position for cid in covers):
            continue
        key = (max(position[cid] for cid in covers), node.seq)
        if best_key is None or key > best_key:
            best, best_key = node, key
    return best


__all__ = ["summary_covers_ids", "active_summary_for_chain"]
