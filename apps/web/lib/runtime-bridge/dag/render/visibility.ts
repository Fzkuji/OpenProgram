/**
 * Renderer: white-fill visibility for nodes whose chat row is on-screen.
 *
 * Two modes:
 *   * ``viewport`` — scan ``#chatArea`` for visible bubbles and mark
 *     the matching DAG nodes with a white fill. No propagation —
 *     only direct bubble hits get the fill.
 *   * ``context``  — bypass chat-scroll entirely; the white-fill marks
 *     the node set the next LLM call will load as context (from
 *     ``/api/sessions/:id/context-range``).
 *
 * Also owns the ``scroll``/``mutation``/manual-wheel wiring that
 * triggers recomputation.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { _applyShapeSize } from "./shapes";
import {
  _chatMutationObserver,
  _chatScrollWired,
  _contextSet,
  _highlightMode,
  _visibleIds,
  setChatMutationObserver,
  setChatScrollWired,
  setVisibleIds,
} from "../store/globals";

export function _applyVisibility(nodeEl: Element, visible: boolean): void {
  let shape: SVGElement | null = null;
  const kids = nodeEl.children;
  for (let i = 0; i < kids.length; i++) {
    const c = kids[i] as SVGElement;
    const tag = c.tagName;
    if (tag !== "circle" && tag !== "polygon" && tag !== "rect") continue;
    // Skip the invisible hit-area circle (pointer-events=all).
    if (c.getAttribute("pointer-events") === "all") continue;
    shape = c;
    break;
  }
  if (shape) {
    _applyShapeSize(shape);
    // Hollow = the canvas colour, never transparent: the fill is what
    // buries the centre-to-centre edge ends inside the outline
    // (shapes.ts) — transparent would show a phantom dot of line cap
    // at the glyph's centre.
    // Filled = the theme's brightest ink (--text-bright): near-white on
    // dark canvases, near-black on light ones. A hardcoded #ffffff was
    // exactly light themes' --bg-primary, so both states painted alike.
    shape.setAttribute(
      "fill",
      visible ? "var(--text-bright, #ffffff)" : "var(--bg-primary, #262624)");
  }
}

export function _setVisibleSet(newSet: Record<string, boolean>): void {
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
  // box (``../canvas.ts``). Yanking it to whatever the transcript last
  // highlighted would fight every pan.
}

export function _recomputeVisibility(): void {
  if (_highlightMode === "context") {
    const newSet: Record<string, boolean> = Object.create(null);
    if (_contextSet) {
      for (const id in _contextSet) newSet[id] = true;
    }
    _setVisibleSet(newSet);
    return;
  }
  const area = document.getElementById("chatArea");
  if (!area) return;
  const container = document.getElementById("chatMessages");
  if (!container) return;
  const rect = area.getBoundingClientRect();
  // The transcript is hidden (the pane is on the DAG perspective, see
  // components/chat/dag-view.tsx), so every bubble measures 0×0. That is
  // "no information", not "nothing is visible" — recomputing here would
  // blank the white fill on every node. Keep the last known set.
  if (rect.width === 0 && rect.height === 0) return;
  const bubbles = container.querySelectorAll("[data-msg-id], [data-msg-ids]");
  const newSet: Record<string, boolean> = Object.create(null);
  for (let i = 0; i < bubbles.length; i++) {
    const br = bubbles[i].getBoundingClientRect();
    if (br.bottom <= rect.top || br.top >= rect.bottom) continue;
    const multi = bubbles[i].getAttribute("data-msg-ids");
    if (multi) {
      const parts = multi.split(/\s+/);
      for (let j = 0; j < parts.length; j++) {
        if (parts[j]) newSet[parts[j]] = true;
      }
    } else {
      const single = bubbles[i].getAttribute("data-msg-id");
      if (single) newSet[single] = true;
    }
  }
  _setVisibleSet(newSet);
}

export function _wireChatScrollSync(): void {
  if (_chatScrollWired) return;
  const area = document.getElementById("chatArea");
  if (!area) return;
  setChatScrollWired(true);
  let raf = 0;
  area.addEventListener(
    "scroll",
    () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        _recomputeVisibility();
      });
    },
    { passive: true },
  );
}

export function _wireChatMutationSync(): void {
  if (typeof MutationObserver === "undefined") return;
  const container = document.getElementById("chatMessages");
  if (!container) return;
  // Disconnect previous observer if any (container may have been
  // replaced by load_session).
  if (_chatMutationObserver) {
    _chatMutationObserver.disconnect();
  }
  let raf = 0;
  const mo = new MutationObserver(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      _recomputeVisibility();
    });
  });
  mo.observe(container, { childList: true, subtree: true });
  setChatMutationObserver(mo);
}
