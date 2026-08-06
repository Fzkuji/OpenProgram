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
 * Folding here rather than in the thread pass keeps the two apart on
 * purpose. The call thread hides an *execution sequence* under its
 * conversation node — a caller-edge relationship. A covered range is a
 * span of the conversation chain itself, so hiding it must not disturb
 * the chain: the summary's own ``predecessor`` already points where the
 * range began, and the node after the range keeps its own predecessor,
 * which ``edges.ts`` follows to the nearest visible ancestor.
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

export function _foldSummaries(graph: GNode[]): SummaryFold {
  const coversOf: Record<string, string[]> = Object.create(null);
  for (const n of graph) {
    const ids = coversIds(n);
    if (ids) coversOf[n.id] = ids;
  }
  const summaryIds = Object.keys(coversOf);
  if (!summaryIds.length) return { visible: graph, coversOf };

  const hidden: Record<string, boolean> = Object.create(null);
  // ``hidden id → the capsule standing in for it``. The node after a
  // folded range re-anchors onto the capsule, not past it: the capsule
  // must sit ON the trunk (rendering.md §9), and leaving the survivor's
  // predecessor pointing into the hidden range makes the generic
  // hop-walk skip THROUGH the capsule to the fork point — the capsule
  // then lays out as a floating sibling branch with no edge.
  const standIn: Record<string, string> = Object.create(null);
  for (const sid of summaryIds) {
    if (_summaryExpanded[sid]) continue;
    for (const id of coversOf[sid]) {
      hidden[id] = true;
      standIn[id] = sid;
    }
  }
  // A capsule is never hidden by another capsule's range: it is the
  // stand-in for its own span, so folding it away would lose the only
  // handle back to those turns.
  for (const sid of summaryIds) {
    delete hidden[sid];
    delete standIn[sid];
  }

  const fullById: Record<string, GNode> = Object.create(null);
  for (const n of graph) fullById[n.id] = n;

  const visible = graph
    .filter((m) => !hidden[m.id])
    .map((m) => {
      const sub = m.predecessor ? standIn[m.predecessor] : undefined;
      // View-only rewrite on a clone — the graph rows themselves stay
      // exactly what the backend sent.
      return sub && sub !== m.id ? { ...m, predecessor: sub } : m;
    });

  // A folded capsule stands where its range stood, so it joins the
  // lane of the survivor now anchored onto it (falling back to the
  // range's first node when the whole branch was covered). The backend
  // lane pass runs on the FULL graph — folding is view state it cannot
  // see — and hands the capsule a fresh sibling lane: correct for the
  // expanded view, floating for the folded one. The frontend lane pass
  // strongly prefers backend lanes, so the override happens here, on
  // the clone.
  for (let i = 0; i < visible.length; i++) {
    const m = visible[i];
    const ids = coversOf[m.id];
    if (!ids) continue;
    if (!_summaryExpanded[m.id]) {
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
    // Expanded: the ghosts are back on screen, and the capsule reads as
    // what they collapsed INTO — so it splices in right after the last
    // covered turn, ahead of the kept tail: ghosts → capsule → tail.
    // Its stored predecessor (where the range began) stays untouched on
    // the backend row; this is the same view-only clone trick as above.
    const covered = new Set(ids);
    const j = visible.findIndex((v) => v.id !== m.id && !covered.has(v.id)
      && !!v.predecessor && covered.has(v.predecessor!));
    const keptFirst = j >= 0 ? visible[j] : undefined;
    let lastCovered = keptFirst?.predecessor;
    if (!lastCovered) {
      for (let k = ids.length - 1; k >= 0; k--) {
        const c = fullById[ids[k]];
        if (c && isChainNode(c)) { lastCovered = c.id; break; }
      }
    }
    if (!lastCovered) continue;
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
