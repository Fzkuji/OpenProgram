/**
 * Pass: apply user/auto collapse to the visible graph.
 *
 * Decides which subtrees to hide based on:
 *   * persistent ``_collapsed`` toggles flipped by click handlers,
 *   * auto-collapse heuristics (``role=tool`` with ≥1 caller-edge kid
 *     always starts folded; non-tool caller clusters fold past the
 *     ``AUTO_COLLAPSE_THRESHOLD``).
 *
 * Reads + writes the shared collapse state in ``../store/globals``
 * (per-session reset on session change, ``_seenCollapsible`` bookkeeping).
 *
 * Returns the visible-only graph, a per-collapsed-root hidden count
 * (for the "+N" badge), and the ``collapsible(node)`` predicate so
 * the renderer can stamp ``data-collapsible="1"`` on the right nodes.
 */

import type { GNode } from "../types";
import { HGW } from "../types";
import {
  _collapsed,
  _collapseSession,
  _seenCollapsible,
  setCollapsed,
  setCollapseSession,
  setSeenCollapsible,
} from "../store/globals";

export function _applyCollapse(graph: GNode[]): {
  visible: GNode[];
  hiddenCount: Record<string, number>;
  isCollapsible: (m: GNode) => boolean;
} {
  const sid = HGW.currentSessionId || null;
  if (sid !== _collapseSession) {
    setCollapsed(Object.create(null));
    setSeenCollapsible(Object.create(null));
    setCollapseSession(sid);
  }
  const childrenOf: Record<string, string[]> = Object.create(null);
  const callerKidsOf: Record<string, string[]> = Object.create(null);
  const internalFlag: Record<string, boolean> = Object.create(null);
  graph.forEach((m) => {
    if (m._internal) internalFlag[m.id] = true;
    const _lp = m.called_by;
    if (_lp) {
      (childrenOf[_lp] = childrenOf[_lp] || []).push(m.id);
    }
    const ca = m.called_by || m.caller;
    if (ca) {
      (callerKidsOf[ca] = callerKidsOf[ca] || []).push(m.id);
    }
  });
  function _internalKids(id: string): string[] {
    return (childrenOf[id] || []).filter((c) => internalFlag[c]);
  }
  function collapsible(m: GNode): boolean {
    if ((callerKidsOf[m.id] || []).length > 0) return true;
    if (m.role === "tool") return (childrenOf[m.id] || []).length > 0;
    if (m._runNode) return _internalKids(m.id).length > 0;
    return false;
  }
  const AUTO_COLLAPSE_THRESHOLD = 4;
  graph.forEach((m) => {
    if (!collapsible(m)) return;
    // Only auto-collapse on first encounter. Once seen, the user's
    // manual toggle is respected — never re-auto-collapse.
    if (_seenCollapsible[m.id]) return;
    _seenCollapsible[m.id] = true;
    if (m.status === "running") return;
    const kidCount = (callerKidsOf[m.id] || []).length;
    if (m.role === "tool" && kidCount > 0) {
      _collapsed[m.id] = true;
    } else if (kidCount > AUTO_COLLAPSE_THRESHOLD) {
      _collapsed[m.id] = true;
    }
  });
  const hidden: Record<string, boolean> = Object.create(null);
  const hiddenCount: Record<string, number> = Object.create(null);
  graph.forEach((m) => {
    if (!_collapsed[m.id]) return;
    const hasCallerKids = (callerKidsOf[m.id] || []).length > 0;
    const stack = hasCallerKids
      ? (callerKidsOf[m.id] || []).slice()
      : m._runNode
        ? _internalKids(m.id)
        : (childrenOf[m.id] || []).slice();
    let cnt = 0;
    while (stack.length) {
      const id = stack.pop()!;
      if (hidden[id]) continue;
      hidden[id] = true;
      cnt++;
      const kids = hasCallerKids
        ? (callerKidsOf[id] || [])
        : (childrenOf[id] || []);
      for (let i = 0; i < kids.length; i++) {
        if (!hasCallerKids && m._runNode && !internalFlag[kids[i]]) continue;
        stack.push(kids[i]);
      }
    }
    hiddenCount[m.id] = cnt;
  });
  return {
    visible: graph.filter((m) => !hidden[m.id]),
    hiddenCount,
    isCollapsible: collapsible,
  };
}
