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
  // Build conv-children index too — we may also fold the synthetic
  // ``[function call] foo(...)`` user-runtime anchor that fn-form
  // writes immediately before the placeholder. Its only purpose is
  // to anchor parent_id on the placeholder; once we collapse the
  // placeholder into its code call, the anchor becomes a redundant
  // dot above the lone surviving square. Fold it away.
  const convKidsOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((m) => {
    if (m.parent_id && byId[m.parent_id]) {
      (convKidsOf[m.parent_id] = convKidsOf[m.parent_id] || []).push(m);
    }
  });
  const removeIds: Record<string, boolean> = Object.create(null);
  const replaceWith: Record<string, string> = Object.create(null);
  const inheritedFrom: Record<string, GNode> = Object.create(null);
  graph.forEach((p) => {
    if (p.display !== "runtime") return;
    const fn = p.function;
    if (!fn) return;
    const kids = callerKidsOf[p.id] || [];
    // Graph payload exposes the function name via the ``function``
    // field on every node (frontend GNode type); ``name`` exists on
    // backend Call objects but doesn't survive the WS serialization.
    const sameNameKid = kids.find(
      (k) => (k.function || (k as { name?: string }).name || "") === fn,
    );
    if (!sameNameKid) return;
    removeIds[p.id] = true;
    replaceWith[p.id] = sameNameKid.id;
    inheritedFrom[sameNameKid.id] = p;
    // fn-form anchor fold: if this placeholder's parent is a
    // user-runtime msg whose only conv-child is the placeholder
    // itself, drop the anchor too and let the surviving code inherit
    // the anchor's parent (= null for a fresh session, or the prior
    // turn's tip otherwise).
    const anchor = p.parent_id ? byId[p.parent_id] : null;
    if (
      anchor
      && anchor.role === "user"
      && anchor.display === "runtime"
      && (convKidsOf[anchor.id] || []).length === 1
    ) {
      removeIds[anchor.id] = true;
      replaceWith[anchor.id] = sameNameKid.id;
    }
  });
  if (!Object.keys(removeIds).length) return { graph, headId };
  if (headId && replaceWith[headId]) headId = replaceWith[headId];

  // Compute per-node tier/depth adjustments so the surviving cluster
  // lands where the topmost removed ancestor was. If only the
  // placeholder is removed, surviving code snaps to the placeholder's
  // slot. If we also removed the fn-form user-runtime anchor, code
  // snaps to the anchor's slot (one row higher).
  const tierDeltaOf: Record<string, number> = Object.create(null);
  const depthDeltaOf: Record<string, number> = Object.create(null);
  // Process each placeholder ONCE; the code call inherits the
  // topmost removed ancestor's geometry (placeholder or anchor).
  const handled: Record<string, boolean> = Object.create(null);
  graph.forEach((p) => {
    if (handled[p.id]) return;
    if (!removeIds[p.id]) return;
    if (p.display !== "runtime") return;
    // ONLY process the placeholder (has a function name). The anchor
    // is also removed but doesn't carry its own caller-tree, so it
    // shouldn't drive a separate geometry walk. Reusing the same
    // forEach across both rows let the anchor iteration overwrite
    // conv-descendant shifts with the placeholder's own depth → 0.
    if (!p.function) return;
    handled[p.id] = true;
    const codeId = replaceWith[p.id];
    if (!codeId) return;
    const code = byId[codeId];
    if (!code) return;
    // Walk up the chain of removed ancestors to find the topmost
    // one. That's the slot the surviving code should occupy.
    let topRemoved: GNode = p;
    let cur: string | null = p.parent_id || null;
    let hops = 0;
    while (cur && removeIds[cur] && hops < 32) {
      const n = byId[cur];
      if (!n) break;
      topRemoved = n;
      cur = n.parent_id || null;
      hops++;
    }
    const tDelta = (topRemoved._tier ?? 0) - (code._tier ?? 0);
    const dDelta = (topRemoved._depth ?? 0) - (code._depth ?? 0);
    // For the surviving code itself + its caller-tree (gui_step etc),
    // apply BOTH tier and depth shift so the subtree slides up + left
    // into the removed-ancestor slot.
    const callerStack: string[] = [codeId];
    while (callerStack.length) {
      const id = callerStack.pop()!;
      if (id in tierDeltaOf) continue;
      tierDeltaOf[id] = tDelta;
      depthDeltaOf[id] = dDelta;
      (callerKidsOf[id] || []).forEach((c) => callerStack.push(c.id));
    }
    // Conv-descendants of the removed placeholder (e.g. follow-up
    // user msg + its reply chain) get reparented onto code. Their
    // depth shift is different from the caller-tree's: they slide UP
    // exactly one row for each REMOVED PLACEHOLDER ancestor between
    // them and root. (The anchor-removal in fn-form contributes only
    // to code's own shift, not to its conv-descendants' — since they
    // were never under anchor in the conv chain, they were under
    // placeholder.)
    //
    // For the simple fn-form case (anchor + placeholder removed,
    // user-msg hangs off placeholder):
    //   * code shift     = topRemoved.depth - code.depth     (= -2)
    //   * user-msg shift = topRemoved.depth - placeholder.depth (= -1)
    const convShift = (topRemoved._depth ?? 0) - (p._depth ?? 0);
    const convDescendants: string[] = [];
    function gatherConv(id: string): void {
      (convKidsOf[id] || []).forEach((c) => {
        if (c.id === codeId) return;
        if (removeIds[c.id]) {
          gatherConv(c.id);
          return;
        }
        if (c.id in depthDeltaOf) return;
        convDescendants.push(c.id);
        gatherConv(c.id);
      });
    }
    gatherConv(p.id);
    convDescendants.forEach((id) => {
      depthDeltaOf[id] = convShift;
    });
  });

  // Resolve the live ancestor for any parent_id that points at a
  // removed node. Walks the chain of removed ids until a survivor or
  // null is reached.
  function liveAncestor(pid: string | null): string | null {
    let cur = pid;
    let hops = 0;
    while (cur && removeIds[cur] && hops < 32) {
      cur = byId[cur]?.parent_id || null;
      hops++;
    }
    return cur;
  }

  const out: GNode[] = [];
  graph.forEach((m) => {
    if (removeIds[m.id]) return;
    let nm: GNode = m;
    // The SAME-NAMED code call (the surviving node) inherits the
    // placeholder's resolved live ancestor — it now occupies the
    // placeholder's slot in the conv chain. If the placeholder's
    // parent (the fn-form anchor) is also being removed, walk further
    // up so the surviving code lands directly under the prior turn.
    //
    // For ANY OTHER node whose parent_id pointed at the removed
    // placeholder (e.g. a follow-up user msg that hung off the
    // placeholder after head_id advanced to it), reparent to the
    // surviving code node so the conv chain stays connected
    // ``code → followup_user → reply`` — that way ``_mergeRuns`` no
    // longer sees code and the user-msg as siblings (which used to
    // make it migrate gui_step / conclusion onto the user dot).
    const placeholderParent = m.parent_id || "";
    if (placeholderParent && replaceWith[placeholderParent]) {
      const codeId = replaceWith[placeholderParent];
      const isSurvivingCode = m.id === codeId;
      if (isSurvivingCode) {
        nm = Object.assign({}, m, {
          parent_id: liveAncestor(placeholderParent),
        });
      } else {
        nm = Object.assign({}, m, { parent_id: codeId });
      }
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
