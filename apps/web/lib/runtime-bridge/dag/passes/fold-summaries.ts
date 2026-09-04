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
 * render raw in full colour, and the capsule stays on screen but is
 * flagged inert (``_summaryInert``): ghost grey, folding nothing. The
 * node set is identical on every branch; only its reading changes.
 *
 * Clicking the capsule flips ``_summaryExpanded`` for that id and the
 * range comes back as ghosts (``_ghost`` on the clones). That state is
 * view-only and never persisted: it says how you are looking at the
 * graph, not what the graph is.
 *
 * Placement (dag/rendering.md §9): the capsule's slot is the covered
 * segment's END, identical in every state and on every branch —
 * expanded it follows its ghosts, on a non-carrying branch it follows
 * the same turns in the raw, and folded is the same slot with the
 * segment collapsed (trunk start). All view-only clone rewrites; the
 * stored row keeps ``predecessor`` = the range's start (ROOT for a
 * from-the-start compaction).
 */

import type { GNode } from "../types";
import { _summaryExpanded } from "../store/globals";
import { isChainNode } from "./thread";

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
  // Apply over the conversation spine only. ``covers_ids`` on the wire
  // also names caller subtrees (tools, spawn roots) so the fold can
  // hide them with the turn; those ids are not on the predecessor
  // walk, and treating them as part of the branch test made every
  // real compact look inert.
  const spineOf = (sid: string): string[] =>
    coversOf[sid].filter((id) => {
      const n = fullById[id];
      return !!n && isChainNode(n, fullById);
    });
  const applies = (sid: string): boolean => {
    if (!chain.size) return false;
    const spine = spineOf(sid);
    return spine.length > 0 && spine.every((id) => chain.has(id));
  };

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
      // A summary this branch's context does not carry: the turns it
      // covers are live here, so it folds nothing and draws grey.
      inert[sid] = true;
      // Seen is seen: the covered turns are on screen raw right now,
      // so when the viewer returns to the carrying branch the range
      // starts open (ghosts) instead of snapping shut again. A fresh
      // session still starts folded — this only flips after a visit.
      // The capsule's own click still folds it back at any time.
      _summaryExpanded[sid] = true;
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
      if (ghost[m.id]) out = { ...out, _ghost: true };
      if (inert[m.id]) out = { ...out, _summaryInert: true };
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
    const ids = coversOf[m.id];
    if (!ids) continue;
    const folded = !inert[m.id] && !_summaryExpanded[m.id];
    if (folded) {
      const successor = visible.find((v) => v.predecessor === m.id);
      const donor = successor ?? fullById[ids[0]];
      if (donor && typeof donor._lane === "number") {
        // _tier too: the backend stamps the capsule with the reply tier
        // (it IS a reply), but as the stand-in for whole turns it sits
        // on the turn column the trunk runs through — the donor's.
        visible[i] = { ...m, _lane: donor._lane, _tier: donor._tier };
      }
      continue;
    }
    // The capsule's slot is the covered segment's END, in every state
    // and on every branch: expanded it follows its ghosts, on a
    // non-carrying branch it follows the same turns in the raw (grey
    // capsule, coloured turns). One position everywhere — folded is
    // just the same slot with the segment collapsed. The splice point
    // is the segment's TIP (last chain node in seq order); matching
    // "any node whose predecessor is covered" would catch dead forks
    // off interior covered turns. View-only clones — the stored
    // predecessor (the range's start) is untouched.
    const covered = new Set(ids);
    let lastCovered: string | undefined;
    for (let k = ids.length - 1; k >= 0; k--) {
      const c = fullById[ids[k]];
      if (c && isChainNode(c, fullById)) { lastCovered = c.id; break; }
    }
    if (!lastCovered) continue;
    const j = visible.findIndex((v) => v.id !== m.id && !covered.has(v.id)
      && v.predecessor === lastCovered);
    const keptFirst = j >= 0 ? visible[j] : undefined;
    const donor = keptFirst ?? fullById[lastCovered];
    visible[i] = {
      ...m,
      predecessor: lastCovered,
      ...(donor && typeof donor._lane === "number"
        ? { _lane: donor._lane, _tier: donor._tier }
        : {}),
    };
    if (keptFirst) visible[j] = { ...keptFirst, predecessor: m.id };
  }
  return { visible, coversOf };
}
