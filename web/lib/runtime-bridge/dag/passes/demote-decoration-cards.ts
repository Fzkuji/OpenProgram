/**
 * Pass: demote LLM-triggered runtime cards so they don't fork the trunk.
 *
 * When an LLM main-reply triggers an ``@agentic_function``, the
 * runtime placeholder card is persisted as a conv-child of the reply
 * (parent_id = reply_id). If the user then sends a follow-up turn,
 * the reply ends up with TWO conv-children: the card and the next
 * user msg. Backend lane.py treats that as a fork and allocates a
 * fresh lane for the new-user subtree, so the figure visually splits
 * into two lanes even though it's a single linear conversation.
 *
 * Fix here: detect "LLM-triggered runtime cards" and re-stamp the
 * lane of the next-turn subtree back onto the parent reply's lane.
 * The card itself is also pinned to the parent's lane so its
 * caller-edge descendants stay on-axis. Legit retries (multiple
 * non-runtime conv-children) are preserved — only the FIRST
 * non-runtime sibling (by ``created_at``) gets promoted.
 *
 * Manually-triggered fn-form cards have ``parent_id`` = a
 * "[function call]" user pseudo-msg (NOT a reply), so they don't
 * match this rule and stay as main-lane peer nodes.
 *
 * Mutates lane fields in place; nothing returned.
 */

import type { GNode } from "../types";

export function _demoteDecorationCards(graph: GNode[]): void {
  if (!graph || !graph.length) return;
  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((m) => {
    byId[m.id] = m;
  });
  const convKidsOf: Record<string, GNode[]> = Object.create(null);
  const callerKidsOf: Record<string, GNode[]> = Object.create(null);
  graph.forEach((m) => {
    if (m.parent_id && byId[m.parent_id]) {
      (convKidsOf[m.parent_id] = convKidsOf[m.parent_id] || []).push(m);
    }
    const ca = (m as { caller?: string }).caller;
    if (ca && byId[ca]) {
      (callerKidsOf[ca] = callerKidsOf[ca] || []).push(m);
    }
  });
  function _isRuntimeCard(n: GNode): boolean {
    return (
      (n.role === "assistant" || n.role === "llm")
      && n.display === "runtime"
    );
  }
  function _isMainReply(n: GNode): boolean {
    return (
      (n.role === "assistant" || n.role === "llm")
      && n.display !== "runtime"
    );
  }
  const affected: Record<string, true> = Object.create(null);
  graph.forEach((p) => {
    if (!_isMainReply(p)) return;
    const kids = convKidsOf[p.id] || [];
    if (kids.length < 2) return;
    const hasCard = kids.some((k) => _isRuntimeCard(k));
    const nonCard = kids.filter((k) => !_isRuntimeCard(k));
    if (!hasCard || !nonCard.length) return;
    affected[p.id] = true;
  });
  if (!Object.keys(affected).length) return;
  Object.keys(affected).forEach((pid) => {
    const p = byId[pid];
    if (typeof p._lane !== "number") return;
    const targetLane = p._lane;
    const kids = (convKidsOf[pid] || []).slice();
    kids.forEach((k) => {
      if (_isRuntimeCard(k)) {
        k._lane = targetLane;
        k._decoration = true;
      }
    });
    const nonRt = kids
      .filter((k) => !_isRuntimeCard(k))
      .sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
    if (!nonRt.length) return;
    const promote = nonRt[0];
    const stack: string[] = [promote.id];
    kids.forEach((k) => {
      if (_isRuntimeCard(k)) stack.push(k.id);
    });
    const seen: Record<string, true> = Object.create(null);
    while (stack.length) {
      const id = stack.pop()!;
      if (seen[id]) continue;
      seen[id] = true;
      const n = byId[id];
      if (!n) continue;
      n._lane = targetLane;
      (convKidsOf[id] || []).forEach((c) => {
        stack.push(c.id);
      });
      (callerKidsOf[id] || []).forEach((c) => {
        stack.push(c.id);
      });
    }
  });
}
