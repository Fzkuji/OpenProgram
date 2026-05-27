/**
 * Pass: collapse a runtime placeholder card into its same-named code call.
 *
 * When an LLM main-reply triggers an ``@agentic_function`` (e.g.
 * gui_agent), backend persists a "runtime placeholder" row that the
 * chat panel turns into a RuntimeBlock. That placeholder has a
 * caller-edge child which is the actual function-call code row. In
 * the mini-DAG those two squares show up back-to-back ("runtime
 * placeholder square → gui_agent code call square") and read as a
 * duplicate visit. This pass drops the placeholder, reparents the
 * code call onto the placeholder's parent slot (one tier deeper
 * than the reply, since it's a side child not on the main chain),
 * and shifts ``_depth`` by ``-1`` for the code call + every
 * caller-edge descendant so the surviving cluster moves up one
 * ``ROW_H``.
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
  graph.forEach((p) => {
    if (p.display !== "runtime") return;
    const fn = p.function;
    if (!fn) return;
    // fn-form path: the placeholder's parent is the synthetic
    // "[function call]" user msg, so the placeholder IS the main-line
    // turn. Keep it visible — collapsing would re-parent the same-
    // named code call as a tier=1 side child off the user msg,
    // pushing the agentic square off the main trunk and leaving the
    // turn's main column with just user/reply circles + triangle.
    // Only collapse the LLM-called case (parent = assistant reply)
    // where the placeholder genuinely duplicates the code call.
    const parent = p.parent_id ? byId[p.parent_id] : null;
    if (!parent || parent.role !== "assistant") return;
    if (parent.display === "runtime") return;
    const kids = callerKidsOf[p.id] || [];
    const sameNameKid = kids.find((k) => (k.name || "") === fn);
    if (!sameNameKid) return;
    removeIds[p.id] = true;
    replaceWith[p.id] = sameNameKid.id;
  });
  if (!Object.keys(removeIds).length) return { graph, headId };
  if (headId && replaceWith[headId]) headId = replaceWith[headId];
  const depthShiftOf: Record<string, true> = Object.create(null);
  Object.keys(replaceWith).forEach((placeholderId) => {
    const codeId = replaceWith[placeholderId];
    const stack: string[] = [codeId];
    while (stack.length) {
      const id = stack.pop()!;
      if (depthShiftOf[id]) continue;
      depthShiftOf[id] = true;
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
    if (depthShiftOf[m.id] && typeof m._depth === "number") {
      nm = nm === m ? Object.assign({}, m) : nm;
      nm._depth = Math.max(0, (nm._depth || 0) - 1);
    }
    out.push(nm);
  });
  return { graph: out, headId };
}
