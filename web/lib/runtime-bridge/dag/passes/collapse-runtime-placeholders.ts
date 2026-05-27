/**
 * Pass: collapse a runtime placeholder card into its same-named code call.
 *
 * Backend writes TWO rows for every ``@agentic_function`` invocation:
 *  1. ``runtime placeholder`` (role=assistant, display=runtime) — the
 *     RuntimeBlock card the chat panel renders.
 *  2. ``code call`` (role=tool, name=<fn>, caller=placeholder) — the
 *     actual function-call entry the @agentic_function decorator wrote.
 *
 * In the mini-DAG these show as two squares back-to-back which reads
 * as a duplicate visit. This pass drops the placeholder + reparents
 * the code call into the placeholder's slot (same parent_id, same
 * tier, same depth) so the user sees ONE square representing the
 * whole call. The code call's caller-tree (gui_step / plan_next_action
 * / conclusion / ...) is auto-collapsed under it by the standard tool-
 * subtree rule and the "+N" badge shows the hidden count.
 *
 * This applies uniformly to BOTH paths:
 *  * LLM-called: parent is the main reply (an assistant row). The
 *    surviving code lands one tier inside the reply's lane — same
 *    spot the placeholder occupied.
 *  * fn-form / direct: parent is the synthetic "[function call]" user
 *    msg. The surviving code lands ON the main trunk at tier=0,
 *    again where the placeholder was.
 *
 * Pure function. Returns transformed graph + possibly-rewritten HEAD.
 */

import type { GNode } from "../types";

export function _collapseRuntimePlaceholders(
  graph: GNode[],
  headId: string | null,
): { graph: GNode[]; headId: string | null } {
  if (!graph || !graph.length) return { graph, headId };
  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((m) => {
    byId[m.id] = m;
  });
  const callerKidsOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((m) => {
    const ca = (m as { caller?: string }).caller;
    if (ca && byId[ca]) {
      (callerKidsOf[ca] = callerKidsOf[ca] || []).push(m);
    }
  });
  const removeIds: Record<string, boolean> = Object.create(null);
  const replaceWith: Record<string, string> = Object.create(null);
  // ``codeFromPlaceholder[placeholderId]`` carries the geometry slots
  // (tier, depth, lane) we want the surviving code node to inherit so
  // it lands exactly where the placeholder was, not one tier off as a
  // caller-child of nothing.
  const inheritedFrom: Record<string, GNode> = Object.create(null);
  graph.forEach((p) => {
    if (p.display !== "runtime") return;
    const fn = p.function;
    if (!fn) return;
    const kids = callerKidsOf[p.id] || [];
    const sameNameKid = kids.find((k) => (k.name || "") === fn);
    if (!sameNameKid) return;
    removeIds[p.id] = true;
    replaceWith[p.id] = sameNameKid.id;
    inheritedFrom[sameNameKid.id] = p;
  });
  if (!Object.keys(removeIds).length) return { graph, headId };
  if (headId && replaceWith[headId]) headId = replaceWith[headId];

  // Compute per-node tier/depth adjustments so the surviving cluster
  // inherits the placeholder's position. We snap the code call to the
  // placeholder's tier/depth, then for each caller-edge descendant we
  // shift by the same deltas so the subtree stays connected.
  const tierDeltaOf: Record<string, number> = Object.create(null);
  const depthDeltaOf: Record<string, number> = Object.create(null);
  Object.keys(replaceWith).forEach((placeholderId) => {
    const codeId = replaceWith[placeholderId];
    const placeholder = byId[placeholderId];
    const code = byId[codeId];
    if (!placeholder || !code) return;
    const tDelta = (placeholder._tier ?? 0) - (code._tier ?? 0);
    const dDelta = (placeholder._depth ?? 0) - (code._depth ?? 0);
    const stack: string[] = [codeId];
    while (stack.length) {
      const id = stack.pop()!;
      if (id in tierDeltaOf) continue;
      tierDeltaOf[id] = tDelta;
      depthDeltaOf[id] = dDelta;
      (callerKidsOf[id] || []).forEach((c) => stack.push(c.id));
    }
  });

  const out: GNode[] = [];
  graph.forEach((m) => {
    if (removeIds[m.id]) return;
    let nm: GNode = m;
    if (replaceWith[m.parent_id || ""]) {
      const removed = byId[m.parent_id!];
      nm = Object.assign({}, m, { parent_id: removed?.parent_id || null });
    }
    const tD = tierDeltaOf[m.id];
    const dD = depthDeltaOf[m.id];
    if ((tD || dD) && typeof m._tier === "number") {
      nm = nm === m ? Object.assign({}, m) : nm;
      if (typeof nm._tier === "number") nm._tier = Math.max(0, nm._tier + (tD || 0));
      if (typeof nm._depth === "number") nm._depth = Math.max(0, nm._depth + (dD || 0));
    }
    out.push(nm);
  });
  return { graph: out, headId };
}
