/**
 * Layout step: assign ``_depth`` (row index) to every node.
 *
 * Prefer the backend-supplied value (``webui/_graph_layout.py`` already
 * computes depth/lane and attaches them to every emitted row). Fall
 * back to "one row per node in list order" only when no node in the
 * graph carries a numeric ``_depth`` — that path is dead in the
 * normal pipeline but kept for safety against malformed inputs.
 *
 * Returns the max-depth value so the caller can size the SVG height.
 */

import type { GNode } from "../types";

export function _assignDepth(
  ordered: GNode[],
  byId: Record<string, GNode>,
): number {
  let maxRow = 0;
  let anyAuthoritative = false;
  ordered.forEach((m) => {
    const n = byId[m.id];
    if (!n) return;
    if (typeof n._depth === "number") {
      anyAuthoritative = true;
      if (n._depth > maxRow) maxRow = n._depth;
    }
  });
  if (anyAuthoritative) {
    Object.keys(byId).forEach((id) => {
      const n = byId[id];
      if (typeof n._depth !== "number") n._depth = 0;
      else if (n._depth > maxRow) maxRow = n._depth;
    });
    return maxRow;
  }
  let row = 0;
  ordered.forEach((m) => {
    const n = byId[m.id];
    if (n && n._depth === undefined) n._depth = row++;
  });
  Object.keys(byId).forEach((id) => {
    if (byId[id]._depth === undefined) byId[id]._depth = row++;
  });
  return Math.max(row - 1, 0);
}
