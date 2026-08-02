/**
 * Pass: fold the range a compaction summary covers behind its capsule.
 *
 * A summary node carries ``covers_ids`` — the ids it stands in for, in
 * seq order, resolved by the backend (``webui/graph_builder.py``) from
 * ``metadata.covers``. Those nodes are still on the chain and still
 * readable; they are simply not what the next request carries, so the
 * default view draws the capsule and elides them (dag/rendering.md §9).
 *
 * Clicking the capsule flips ``_summaryExpanded`` for that id and the
 * range comes back as ghosts. That state is view-only and never
 * persisted: it says how you are looking at the graph, not what the
 * graph is.
 *
 * Folding here rather than in ``apply-collapse`` keeps the two apart on
 * purpose. ``_collapsed`` hides an *execution subtree* under its
 * conversation node — a caller-edge relationship. A covered range is a
 * span of the conversation chain itself, so hiding it must not disturb
 * the chain: the summary's own ``predecessor`` already points where the
 * range began, and the node after the range keeps its own predecessor,
 * which ``edges.ts`` follows to the nearest visible ancestor.
 */

import type { GNode } from "../types";
import { _summaryExpanded } from "../store/globals";

export interface SummaryFold {
  /** Visible graph — covered nodes removed for every folded capsule. */
  visible: GNode[];
  /** ``summaryId → covered ids`` for every summary in the graph,
   *  expanded or not. The renderer needs it either way: folded it
   *  draws the pleats and the "covers N" label, expanded it marks
   *  those nodes as ghosts. */
  coversOf: Record<string, string[]>;
}

/** The ids a summary node covers, or null when the node is not one. */
export function coversIds(n: GNode): string[] | null {
  const raw = (n as Record<string, unknown>).covers_ids;
  if (!Array.isArray(raw) || !raw.length) return null;
  return raw.map(String);
}

export function _foldSummaries(graph: GNode[]): SummaryFold {
  const coversOf: Record<string, string[]> = Object.create(null);
  for (const n of graph) {
    const ids = coversIds(n);
    if (ids) coversOf[n.id] = ids;
  }
  const summaryIds = Object.keys(coversOf);
  if (!summaryIds.length) return { visible: graph, coversOf };

  const hidden: Record<string, boolean> = Object.create(null);
  for (const sid of summaryIds) {
    if (_summaryExpanded[sid]) continue;
    for (const id of coversOf[sid]) hidden[id] = true;
  }
  // A capsule is never hidden by another capsule's range: it is the
  // stand-in for its own span, so folding it away would lose the only
  // handle back to those turns.
  for (const sid of summaryIds) delete hidden[sid];

  return { visible: graph.filter((m) => !hidden[m.id]), coversOf };
}
