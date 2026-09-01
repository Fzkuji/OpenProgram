import type { GNode } from "../types";
import { isSpawnRoot, isTopProgramRun } from "./thread";

/** Project user-invoked Programs as ordered ROOT actions in the overview.
 * Persisted predecessor remains untouched for checkout and context. */
export function projectTopPrograms(graph: GNode[]): GNode[] {
  const root = graph.find((node) => node.display === "root");
  if (!root) return graph;
  const rootLane = typeof root._lane === "number" ? root._lane : 0;
  const owners = new Set<string>();
  let changed = false;
  let projected = graph.map((node) => {
    if (!isTopProgramRun(node) || node.retry_of) return node;
    owners.add(node.id);
    if (node._overview_parent === root.id
        && node._lane === rootLane && node._tier === 1) return node;
    changed = true;
    return { ...node, _overview_parent: root.id, _lane: rootLane, _tier: 1 };
  });
  let added = true;
  while (added) {
    added = false;
    graph.forEach((node) => {
      if (owners.has(node.id) || isSpawnRoot(node)) return;
      const caller = String(node.caller || "");
      if (!caller || !owners.has(caller)) return;
      owners.add(node.id);
      added = true;
    });
  }
  projected = projected.map((node) => {
    if (!owners.has(node.id) || isTopProgramRun(node)
        || node._lane === rootLane) return node;
    changed = true;
    return { ...node, _lane: rootLane };
  });
  return changed ? projected : graph;
}
