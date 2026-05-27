/**
 * Pass: merge tool wrapper rows into the surviving run-node.
 *
 * For every parent that has both a ``role=tool`` row AND a non-tool
 * sibling (the "real" run node — usually the LLM-driven function
 * call), pick the earliest tool row preceding the run kid, remove
 * the tool wrapper, mark the run kid as ``_runNode`` and reparent
 * the wrapper's internal subtree under the run kid. Internal nodes
 * carry their ``_tier`` minus the wrapper's tier so the surviving
 * cluster sits exactly one ``COL_W`` to the right of its visible
 * run-node — without this they keep the removed tool's tier and
 * end up offset by the full call-stack depth.
 *
 * Pure function. Returns transformed graph + possibly-rewritten HEAD.
 */

import type { GNode } from "../types";

export function _mergeRuns(
  graph: GNode[],
  headId: string | null,
): { graph: GNode[]; headId: string | null } {
  if (!graph || !graph.length) return { graph, headId };
  const idx: Record<string, number> = Object.create(null);
  graph.forEach((m, i) => {
    idx[m.id] = i;
  });
  const kidsOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((m) => {
    if (m.parent_id) (kidsOf[m.parent_id] = kidsOf[m.parent_id] || []).push(m);
  });
  const removeIds: Record<string, boolean> = Object.create(null);
  const runNode: Record<string, boolean> = Object.create(null);
  const mergeTarget: Record<string, string> = Object.create(null);
  const internalOf: Record<string, string> = Object.create(null);
  const tierShift: Record<string, number> = Object.create(null);
  Object.keys(kidsOf).forEach((pid) => {
    const kids = kidsOf[pid];
    const tools = kids.filter((k) => k.role === "tool");
    if (!tools.length) return;
    tools.sort((a, b) => idx[a.id] - idx[b.id]);
    kids.forEach((k) => {
      if (k.role === "tool") return;
      let t: GNode | null = null;
      for (let i = 0; i < tools.length; i++) {
        if (idx[tools[i].id] < idx[k.id]) t = tools[i];
        else break;
      }
      if (!t || removeIds[t.id]) return;
      removeIds[t.id] = true;
      mergeTarget[t.id] = k.id;
      runNode[k.id] = true;
      const shift =
        (typeof t._tier === "number" ? t._tier : 0)
        - (typeof k._tier === "number" ? k._tier : 0);
      const stack = (kidsOf[t.id] || []).slice();
      while (stack.length) {
        const ic = stack.pop()!;
        if (ic.id in internalOf) continue;
        internalOf[ic.id] = k.id;
        if (shift > 0) tierShift[ic.id] = shift;
        (kidsOf[ic.id] || []).forEach((g) => stack.push(g));
      }
    });
  });
  if (!Object.keys(removeIds).length) return { graph, headId };
  if (headId && mergeTarget[headId]) headId = mergeTarget[headId];

  const reparent: Record<string, string> = Object.create(null);
  graph.forEach((m) => {
    const pid = m.parent_id;
    if (!pid) return;
    if (m.id in internalOf) {
      if (removeIds[pid]) reparent[m.id] = internalOf[m.id];
    } else if (pid in internalOf) {
      reparent[m.id] = internalOf[pid];
    }
  });

  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((m) => {
    byId[m.id] = m;
  });
  function _build(m: GNode): GNode {
    let nm: GNode | null = null;
    if (m.id in reparent) {
      nm = Object.assign({}, m);
      nm.parent_id = reparent[m.id];
    }
    if (m.id in internalOf) {
      nm = nm || Object.assign({}, m);
      nm._internal = true;
      const sh = tierShift[m.id];
      if (sh) {
        const curT = typeof nm._tier === "number" ? nm._tier : 0;
        nm._tier = Math.max(0, curT - sh);
      }
    }
    if (runNode[m.id]) {
      nm = nm || Object.assign({}, m);
      nm._runNode = true;
    }
    return nm || m;
  }
  const emitted: Record<string, boolean> = Object.create(null);
  const out: GNode[] = [];
  graph.forEach((m) => {
    if (removeIds[m.id]) {
      const tgt = mergeTarget[m.id];
      if (tgt && !emitted[tgt] && byId[tgt]) {
        emitted[tgt] = true;
        out.push(_build(byId[tgt]));
      }
      return;
    }
    if (emitted[m.id]) return;
    emitted[m.id] = true;
    out.push(_build(m));
  });
  return { graph: out, headId };
}
