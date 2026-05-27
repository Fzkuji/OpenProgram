/**
 * Layout step: turn the flat ``GNode[]`` into a parent/children tree.
 *
 * Output:
 *   * ``byId``  — id-indexed map with ``children`` arrays filled in.
 *   * ``roots`` — nodes whose ``parent_id`` is missing or unknown.
 *
 * Children + roots are sorted by ``created_at`` so deterministic order
 * matches the time the user actually saw each turn.
 */

import type { GNode } from "../types";

export function _buildTree(graph: GNode[]): {
  roots: GNode[];
  byId: Record<string, GNode>;
} {
  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((m) => {
    byId[m.id] = Object.assign({ children: [] }, m);
  });
  const roots: GNode[] = [];
  graph.forEach((m) => {
    const node = byId[m.id];
    if (m.parent_id && byId[m.parent_id]) byId[m.parent_id].children!.push(node);
    else roots.push(node);
  });
  function byTs(a: GNode, b: GNode): number {
    return (a.created_at || 0) - (b.created_at || 0);
  }
  roots.sort(byTs);
  Object.keys(byId).forEach((id) => byId[id].children!.sort(byTs));
  return { roots, byId };
}
