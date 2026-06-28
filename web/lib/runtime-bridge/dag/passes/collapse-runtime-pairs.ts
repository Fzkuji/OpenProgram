/**
 * Pass: collapse runtime user/assistant pairs.
 *
 * Pre-existing user/assistant pair where both rows are
 * ``display=runtime`` and the assistant is the user's only child →
 * fold the user into the assistant (remove the user row, reparent
 * the assistant to the user's parent). This is the legacy "tool
 * wrapper" pattern from before the explicit ``caller``-edge schema:
 * the wrapper row had no chat content of its own, so showing it as
 * a separate node duplicates the column.
 *
 * Pure function. Returns the transformed graph + a possibly-rewritten
 * HEAD id (if HEAD pointed at one of the removed user rows it gets
 * rebound to its surviving assistant child).
 */

import type { GNode } from "../types";

export function _collapseRuntimePairs(
  graph: GNode[],
  headId: string | null,
): { graph: GNode[]; headId: string | null } {
  if (!graph || !graph.length) return { graph, headId };
  const childrenOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((m) => {
    const _lp = m.called_by;
    if (_lp) (childrenOf[_lp] = childrenOf[_lp] || []).push(m);
  });
  const removeIds: Record<string, boolean> = Object.create(null);
  const reparent: Record<string, string | null> = Object.create(null);
  const userToAsst: Record<string, string> = Object.create(null);
  graph.forEach((m) => {
    if (m.role !== "user" || m.display !== "runtime") return;
    const kids = childrenOf[m.id] || [];
    if (kids.length !== 1) return;
    const c = kids[0];
    if (c.role !== "assistant" || c.display !== "runtime") return;
    removeIds[m.id] = true;
    reparent[c.id] = m.called_by || null;
    userToAsst[m.id] = c.id;
  });
  if (headId && userToAsst[headId]) headId = userToAsst[headId];
  const collapsed: GNode[] = [];
  graph.forEach((m) => {
    if (removeIds[m.id]) return;
    if (m.id in reparent) {
      collapsed.push(Object.assign({}, m, { called_by: reparent[m.id], parent_id: reparent[m.id] }));
    } else {
      collapsed.push(m);
    }
  });
  return { graph: collapsed, headId };
}
