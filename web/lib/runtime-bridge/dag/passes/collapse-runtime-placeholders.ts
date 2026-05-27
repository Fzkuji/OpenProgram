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
    // fn-form anchor fold: the synthetic ``[function call] foo(...)``
    // user-runtime msg has no semantic content in the visible chat —
    // chat panel hides it (display=runtime + role=user → return null).
    // In mini-DAG we likewise want to hide it so the user sees
    // ``code square → follow-up user msg`` directly, not
    // ``anchor circle → fork → [code square, user msg]``.
    //
    // backend ``linear_history`` filters runtime placeholders out of
    // the user-visible chain, so the next chat turn's user msg gets
    // ``parent_id = anchor`` instead of placeholder. That makes anchor
    // appear to have TWO conv-children (placeholder + new user msg)
    // which lane.py turns into a fork. Fold the anchor here so the
    // new user msg gets reparented onto the surviving code instead,
    // keeping the mini-DAG on a single trunk.
    const anchor = p.parent_id ? byId[p.parent_id] : null;
    if (
      anchor
      && anchor.role === "user"
      && anchor.display === "runtime"
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
    // Conv-descendants of any removed ancestor get reparented onto
    // code. The depth shift depends on WHICH removed ancestor each
    // conv-child was hanging off:
    //   shift = topRemoved.depth - directRemovedAncestor.depth
    //
    // For anchor's direct kid (e.g. follow-up user msg with
    // parent=anchor): shift = 0 - 0 = 0 (already at the right row).
    // For placeholder's direct kid: shift = 0 - 1 = -1.
    //
    // Conv-descendants of a NON-removed survivor inherit the parent's
    // shift implicitly (parent's depth was shifted; child's old depth
    // - parent's old depth = same delta from new parent.depth → no
    // additional shift needed).
    const topRemovedDepth = topRemoved._depth ?? 0;
    const topRemovedTier = topRemoved._tier ?? 0;
    function gatherConv(id: string, hostRemoved: GNode): void {
      (convKidsOf[id] || []).forEach((c) => {
        if (c.id === codeId) return;
        if (removeIds[c.id]) {
          gatherConv(c.id, c);
          return;
        }
        if (c.id in depthDeltaOf) return;
        depthDeltaOf[c.id] = topRemovedDepth - (hostRemoved._depth ?? 0);
        // Backend's tier-from-caller-chain walk gives the placeholder
        // tier=hostRemoved.tier+1 (placeholder.caller = anchor, so
        // tier rolls up off the anchor's caller chain). Any conv-kid
        // hanging off the placeholder inherits that bumped tier even
        // though semantically it's a regular main-trunk msg. Apply
        // the same delta as depth so the conv-descendant slides back
        // to the topRemoved's tier slot.
        const tierShiftSame = topRemovedTier - (hostRemoved._tier ?? 0);
        if (tierShiftSame !== 0) tierDeltaOf[c.id] = tierShiftSame;
        // Survivor child's own conv-descendants inherit the SAME
        // depth + tier shift because they slid as one block with
        // their parent. Walk through any further removed-ancestor
        // pockets (e.g. the LLM-called placeholder hanging off a
        // reply mid-chain) so their post-collapse survivor descendants
        // also get shifted.
        const sameShift = depthDeltaOf[c.id];
        const sameTier = tierDeltaOf[c.id];
        const stack2: string[] = [c.id];
        while (stack2.length) {
          const nid = stack2.pop()!;
          (convKidsOf[nid] || []).forEach((cc) => {
            if (cc.id === codeId) return;
            if (removeIds[cc.id]) {
              stack2.push(cc.id);
              return;
            }
            if (cc.id in depthDeltaOf) return;
            depthDeltaOf[cc.id] = sameShift;
            if (sameTier) tierDeltaOf[cc.id] = sameTier;
            stack2.push(cc.id);
          });
        }
      });
    }
    gatherConv(topRemoved.id, topRemoved);
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
