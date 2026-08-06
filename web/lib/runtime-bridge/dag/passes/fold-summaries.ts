/**
 * Pass: fold the range a compaction summary covers behind its capsule.
 *
 * A summary node carries ``covers_ids`` — the ids it stands in for, in
 * seq order, resolved by the backend (``webui/graph_builder.py``) from
 * ``metadata.covers_ids``.
 *
 * The fold is PER-BRANCH (dag/rendering.md §9): a summary belongs to
 * the branch whose active chain contains its whole covered segment.
 * Only there does the capsule fold the range (or, expanded, mark it as
 * ghosts). On any other branch those turns ARE the live context — they
 * render raw in full colour, and the capsule itself is flagged inert
 * (``_summaryInert``) so the renderer dims it instead.
 *
 * Clicking the capsule flips ``_summaryExpanded`` for that id and the
 * range comes back as ghosts (``_ghost`` on the clones). That state is
 * view-only and never persisted: it says how you are looking at the
 * graph, not what the graph is.
 *
 * The capsule's drawn edge is its stored edge — ``predecessor`` = the
 * covered range's own start (ROOT for a from-the-start compaction), so
 * compaction reads as a fork at the session's start: the capsule is an
 * alternative version of the opening turns, in the same scene-3
 * vocabulary as a retry branch. The only view-side adjustment is the
 * folded capsule inheriting the survivor's lane/tier, because the
 * backend lane pass runs on the FULL graph and cannot see the fold.
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

export function _foldSummaries(
  graph: GNode[],
  headId: string | null,
): SummaryFold {
  const coversOf: Record<string, string[]> = Object.create(null);
  for (const n of graph) {
    const ids = coversIds(n);
    if (ids) coversOf[n.id] = ids;
  }
  const summaryIds = Object.keys(coversOf);
  if (!summaryIds.length) return { visible: graph, coversOf };

  const fullById: Record<string, GNode> = Object.create(null);
  for (const n of graph) fullById[n.id] = n;

  // The active chain — the branch the viewer is on. A summary applies
  // here iff its whole covered segment lies on this chain.
  const chain = new Set<string>();
  let cur = headId ? fullById[headId] : undefined;
  while (cur && !chain.has(cur.id)) {
    chain.add(cur.id);
    cur = cur.predecessor ? fullById[cur.predecessor] : undefined;
  }
  const applies = (sid: string): boolean =>
    chain.size > 0 && coversOf[sid].every((id) => chain.has(id));

  const hidden: Record<string, boolean> = Object.create(null);
  // ``hidden id → the capsule standing in for it``: the node after a
  // folded range re-anchors onto the capsule so the trunk stays one
  // line (folded view only).
  const standIn: Record<string, string> = Object.create(null);
  // Covered ids of applying, EXPANDED capsules — drawn as ghosts.
  const ghost: Record<string, boolean> = Object.create(null);
  const inert: Record<string, boolean> = Object.create(null);

  for (const sid of summaryIds) {
    if (!applies(sid)) {
      inert[sid] = true;
      continue;
    }
    if (_summaryExpanded[sid]) {
      for (const id of coversOf[sid]) ghost[id] = true;
      continue;
    }
    for (const id of coversOf[sid]) {
      hidden[id] = true;
      standIn[id] = sid;
    }
  }
  // A capsule is never hidden or ghosted by another capsule's range:
  // it is the stand-in for its own span, so folding it away would lose
  // the only handle back to those turns.
  for (const sid of summaryIds) {
    delete hidden[sid];
    delete standIn[sid];
    delete ghost[sid];
  }

  const visible = graph
    .filter((m) => !hidden[m.id])
    .map((m) => {
      // View-only rewrites on clones — the graph rows themselves stay
      // exactly what the backend sent.
      const sub = m.predecessor ? standIn[m.predecessor] : undefined;
      let out = m;
      if (sub && sub !== m.id) out = { ...out, predecessor: sub };
      if (ghost[m.id]) out = out === m ? { ...m, _ghost: true } : { ...out, _ghost: true };
      if (inert[m.id]) out = out === m ? { ...m, _summaryInert: true } : { ...out, _summaryInert: true };
      return out;
    });

  // A folded capsule stands where its range stood, so it joins the
  // lane of the survivor now anchored onto it (falling back to the
  // range's first node when the whole branch was covered). The backend
  // lane pass runs on the FULL graph — folding is view state it cannot
  // see — and hands the capsule a fresh sibling lane: correct for the
  // expanded view, floating for the folded one.
  for (let i = 0; i < visible.length; i++) {
    const m = visible[i];
    if (!coversOf[m.id]) continue;
    if (inert[m.id] || _summaryExpanded[m.id]) continue;
    const successor = visible.find((v) => v.predecessor === m.id);
    const donor = successor ?? fullById[coversOf[m.id][0]];
    if (donor && typeof donor._lane === "number") {
      // _tier too: the backend stamps the capsule with the reply tier
      // (it IS a reply), but as the stand-in for whole turns it sits
      // on the turn column the trunk runs through — the donor's.
      visible[i] = { ...m, _lane: donor._lane, _tier: donor._tier };
    }
  }
  return { visible, coversOf };
}
