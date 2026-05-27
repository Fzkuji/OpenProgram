/**
 * Pass: apply d3-hierarchy's Reingold-Tilford tree layout to function-
 * call subtrees, leaving the conv-chain trunk on the legacy linear
 * column.
 *
 * Why this pass exists
 * --------------------
 * The legacy layout puts every node at x = PAD_X + lane*COL_W +
 * tier*COL_W. For a normal conv (user → reply → user → reply) plus
 * one or two tool_use calls per reply this works, but inside a
 * complex ``@agentic_function`` body where one call branches into 5+
 * sub-calls that themselves branch further, multiple siblings share
 * the same tier — they pile up in one column.
 *
 * The fix is the standard Reingold-Tilford tree layout (1981):
 *   1. Post-order walk computes ``subtree_width`` for each node = max
 *      of (1, sum of children widths).
 *   2. Pre-order walk assigns ``x`` to each node = parent.x +
 *      cumulative width of left siblings + own midpoint.
 * d3-hierarchy ships a battle-tested implementation that we plug in
 * here without re-inventing the algorithm.
 *
 * Scope: ONLY function-call subtrees get re-laid-out. The trunk
 * (user/reply chain) keeps the linear column the legacy pass
 * produced. That preserves the "conversation timeline" reading of
 * the panel — function calls fan out to the right of the reply that
 * spawned them, and only that subtree uses tree-layout coords.
 *
 * Output
 * ------
 * Each function-call subtree node gets ``_x`` / ``_y`` (absolute
 * pixel positions). The pipeline's ``pos()`` prefers these when set,
 * falling back to ``lane * COL_W + tier * COL_W`` for trunk nodes.
 */

import { hierarchy, tree as d3Tree } from "d3-hierarchy";

import { COL_W, PAD_X, PAD_Y, ROW_H, type GNode } from "../types";

interface NodeWithChildren {
  id: string;
  node: GNode;
  children: NodeWithChildren[];
}

export function _applyD3TreeLayout(graph: GNode[]): void {
  if (!graph.length) return;

  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((n) => { byId[n.id] = n; });

  // Caller-children index: a node's caller-children form its
  // function-call subtree (sub-calls made from inside it).
  const callerKidsOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((n) => {
    const ca = (n as { caller?: string }).caller;
    if (ca && byId[ca]) {
      (callerKidsOf[ca] = callerKidsOf[ca] || []).push(n);
    }
  });

  // A node is a "subtree root" if:
  //   * it has caller-children (so its descendants form a subtree), AND
  //   * either its caller is empty/not-in-graph (top-level call) OR
  //     its caller belongs to the trunk (i.e. is a regular conv msg
  //     with no further caller chain itself).
  // The simplest invariant: only run d3 layout on nodes whose caller
  // is NOT itself a tool/function call. That puts the subtree root
  // exactly at the boundary between trunk and function-call tree.
  function isSubtreeRoot(n: GNode): boolean {
    if (!callerKidsOf[n.id] || callerKidsOf[n.id].length === 0) return false;
    const ca = (n as { caller?: string }).caller;
    if (!ca) return true;
    const caller = byId[ca];
    if (!caller) return true;
    return caller.role !== "tool" && !caller.function;
  }

  // Snapshot legacy positions FIRST so trunk nodes (the ones not
  // touched by d3) keep their existing coords. We only override _x /
  // _y for nodes inside a function-call subtree.
  graph.forEach((n) => {
    if (typeof n._tier === "number" && typeof n._depth === "number") {
      n._x = PAD_X + ((n._lane || 0) + n._tier) * COL_W;
      n._y = PAD_Y + n._depth * ROW_H;
    }
  });

  // For each subtree root, build a hierarchy and run d3.tree.
  graph.forEach((root) => {
    if (!isSubtreeRoot(root)) return;
    const built: NodeWithChildren = buildTree(root, callerKidsOf, byId);
    const root3 = hierarchy<NodeWithChildren>(built, (d) => d.children);
    // nodeSize: [horizontal spread per node, vertical spread per
    // depth level]. The horizontal value defines minimum separation
    // between sibling subtrees; the vertical value is the row height
    // for tree layers. Mini-DAG already uses COL_W/ROW_H so reuse.
    d3Tree<NodeWithChildren>().nodeSize([COL_W, ROW_H])(root3);

    // Anchor the subtree at the legacy position of its root. d3
    // returns x relative to the layout's own origin; translate so
    // root3.x maps to root.legacy_x.
    const rootX = root._x ?? PAD_X;
    const rootY = root._y ?? PAD_Y;
    const offsetX = rootX - (root3.x ?? 0);
    const offsetY = rootY - (root3.y ?? 0);
    root3.each((d3node) => {
      const n = d3node.data.node;
      n._x = (d3node.x ?? 0) + offsetX;
      n._y = (d3node.y ?? 0) + offsetY;
    });
  });
}

function buildTree(
  root: GNode,
  callerKidsOf: Record<string, GNode[]>,
  byId: Record<string, GNode>,
): NodeWithChildren {
  const seen: Record<string, boolean> = Object.create(null);
  function walk(n: GNode): NodeWithChildren {
    if (seen[n.id]) {
      return { id: n.id, node: n, children: [] };
    }
    seen[n.id] = true;
    const kids = callerKidsOf[n.id] || [];
    return {
      id: n.id,
      node: n,
      children: kids
        .map((k) => byId[k.id])
        .filter(Boolean)
        .map((k) => walk(k)),
    };
  }
  return walk(root);
}
