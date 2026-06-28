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

import { _applyShapeSize } from "../shapes";
import {
  _chatMutationObserver,
  _chatScrollWired,
  _contextSet,
  _highlightMode,
  _panelResizeWired,
  _userScrolledGraph,
  _userScrollTimer,
  _visibleIds,
  setChatMutationObserver,
  setChatScrollWired,
  setPanelResizeWired,
  setUserScrolledGraph,
  setUserScrollTimer,
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
    shape.setAttribute("fill", visible ? "#ffffff" : "transparent");
  }
}

export function _setVisibleSet(newSet: Record<string, boolean>): void {
  const panel = document.getElementById("historyPanel");
  if (!panel) return;
  const body = panel.querySelector(".history-body") as HTMLElement | null;
  if (!body) return;
  const visibleEls: Element[] = [];
  body.querySelectorAll(".history-node").forEach((g) => {
    const id = g.getAttribute("data-msg-id") || "";
    const nowVisible = !!newSet[id];
    const wasVisible = !!_visibleIds[id];
    if (nowVisible !== wasVisible) _applyVisibility(g, nowVisible);
    if (nowVisible) visibleEls.push(g);
  });
  setVisibleIds(newSet);

  if (visibleEls.length && !_userScrolledGraph) {
    const mid = visibleEls[Math.floor(visibleEls.length / 2)];
    const nodeRect = mid.getBoundingClientRect();
    const bodyRect = body.getBoundingClientRect();
    const nodeY = nodeRect.top - bodyRect.top + body.scrollTop;
    const desired = body.clientHeight * 0.45;
    let targetScroll = nodeY - desired;
    const maxScroll = Math.max(0, body.scrollHeight - body.clientHeight);
    if (targetScroll < 0) targetScroll = 0;
    if (targetScroll > maxScroll) targetScroll = maxScroll;
    if (Math.abs(targetScroll - body.scrollTop) > 24) {
      body.scrollTo({ top: targetScroll, behavior: "smooth" });
    }
  }
}

export function _wireGraphManualScroll(): void {
  const body = document.querySelector("#historyPanel .history-body") as
    | (HTMLElement & { _manualScrollWired?: boolean })
    | null;
  if (!body || body._manualScrollWired) return;
  body._manualScrollWired = true;
  body.addEventListener(
    "wheel",
    () => {
      setUserScrolledGraph(true);
      clearTimeout(_userScrollTimer);
      setUserScrollTimer(window.setTimeout(() => {
        setUserScrolledGraph(false);
      }, 1500));
    },
    { passive: true },
  );
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
        _wireGraphManualScroll();
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

export function _wirePanelResize(onResize: () => void): void {
  if (_panelResizeWired) return;
  if (typeof ResizeObserver === "undefined") return;
  const panel = document.getElementById("historyPanel");
  if (!panel) return;
  const body = panel.querySelector(".history-body") as HTMLElement | null;
  if (!body) return;
  setPanelResizeWired(true);
  let lastW = body.clientWidth;
  let raf = 0;
  const ro = new ResizeObserver(() => {
    const w = body.clientWidth;
    if (w === lastW) return;
    lastW = w;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      onResize();
    });
  });
  ro.observe(body);
}
