/**
 * Renderer: the coverage fill (dag/rendering.md §8). A node whose turn
 * the next LLM call will load as context (``_contextSet``, from
 * ``/api/sessions/:id/context-range``) is a full block of its tone;
 * the rest are faint. Repainted when the set arrives and after every
 * draw.
 */

import {
  _contextSet,
  _visibleIds,
  setVisibleIds,
} from "../store/globals";

function _applyVisibility(nodeEl: Element, visible: boolean): void {
  // Covered = part of what the next request carries (dag/rendering.md §8):
  // the tile is a full block of its tone; outside it, a faint one.
  // The colours live in app/styles/dag/graph/nodes.css.
  nodeEl.classList.toggle("is-covered", visible);
}

function _setVisibleSet(newSet: Record<string, boolean>): void {
  const panel = document.getElementById("historyPanel");
  if (!panel) return;
  const body = panel.querySelector(".history-body") as HTMLElement | null;
  if (!body) return;
  body.querySelectorAll(".history-node").forEach((g) => {
    const id = g.getAttribute("data-msg-id") || "";
    const nowVisible = !!newSet[id];
    const wasVisible = !!_visibleIds[id];
    if (nowVisible !== wasVisible) _applyVisibility(g, nowVisible);
  });
  setVisibleIds(newSet);
  // No auto-scroll: the canvas is a camera the user drives, not a scroll
  // box (``../interaction/view/canvas.ts``). Yanking it to whatever the transcript last
  // highlighted would fight every pan.
}

export function _recomputeVisibility(): void {
  const newSet: Record<string, boolean> = Object.create(null);
  if (_contextSet) {
    for (const id in _contextSet) newSet[id] = true;
  }
  _setVisibleSet(newSet);
}
