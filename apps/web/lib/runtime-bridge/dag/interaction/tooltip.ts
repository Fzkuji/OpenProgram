/**
 * History DAG node card — ONE window, two states.
 *
 * The same ``.history-tooltip`` element serves both interactions
 * (dag/rendering.md §11); there is never a second window:
 *
 *   * **Hover** → the brief state: what the node is, its tokens, a
 *     short output preview, its folded call count. Appears after a
 *     short delay so sweeping the cursor doesn't strobe;
 *     ``pointer-events: none``; gone on mouse-off.
 *   * **Right-click** → the SAME card expands in place
 *     (``expandTooltip``): full fields, longer previews, coverage /
 *     context / id, and the verbs (render/inspector.ts builds them)
 *     appended below. The card turns interactive and stays until a
 *     click lands elsewhere; hover cannot re-summon the brief state
 *     over it.
 *   * **Click** → the node's own action only (fold/unfold its thread).
 *
 * No row labels are reinvented — each line uses the actual schema key
 * (``name`` / ``input`` / ``output`` / ``model`` / ``label`` / ...),
 * plus the facts only the picture knows: the node's context-coverage
 * state and its folded call count, both read off the DOM flags the
 * node drawer stamped so card and picture cannot disagree.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

import { type GNode } from "../types";
import {
  bodyText as _bodyText,
  clampText,
  kindLabel,
  tooltipRows,
} from "./tooltip-content";
export { bodyText as _bodyText } from "./tooltip-content";

const SHOW_DELAY_MS = 450;   // hover-stay before the card appears
const BRIEF_CHARS = 120;     // chars per body block, hover cut
const DETAIL_CHARS = 600;    // chars per body block, right-click cut
const GAP = 10;              // px gap between node and card

let _tooltip: HTMLDivElement | null = null;
let _showTimer = 0;
let _currentId: string | null = null;
let _detailOpen = false;

export function ensureTooltip(body: HTMLElement): HTMLDivElement {
  if (_tooltip && _tooltip.parentElement === body) return _tooltip;
  _tooltip = document.createElement("div");
  _tooltip.className = "history-tooltip";
  body.appendChild(_tooltip);
  return _tooltip;
}

/** Hide the hover state. A no-op while the card is expanded: mouse-off
 *  fires this, and the expanded card must survive the pointer's trip
 *  down to its own verbs. ``closeTooltipDetail`` is the way down. */
export function hideTooltip(): void {
  if (_detailOpen) return;
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
  _currentId = null;
  if (_tooltip) _tooltip.classList.remove("visible");
}

/** Collapse the expanded card entirely (elsewhere-click, verb run,
 *  re-render). Hover starts over from nothing afterwards. */
export function closeTooltipDetail(): void {
  if (!_detailOpen) return;
  _detailOpen = false;
  if (_tooltip) _tooltip.classList.remove("detail");
  hideTooltip();
}

export function resetTooltip(): void {
  _tooltip = null;
  _currentId = null;
  _detailOpen = false;
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
}

/** Show the brief card for ``node`` next to ``nodeRect`` (viewport
 *  coordinates). ``el`` is the node's ``<g>`` — the drawer's data-*
 *  flags on it carry the coverage / thread facts. Idempotent on
 *  repeated calls with the same node. */
export function showTooltip(
  body: HTMLElement,
  node: GNode,
  nodeRect: DOMRect,
  el?: Element | null,
): void {
  // While the card is expanded it belongs to the right-click, and the
  // raw-JSON layer is a reading surface of its own — hover must not
  // repaint either out from under the user. Gating at the entry (not
  // just once at expand time) is what holds: the cursor is still on
  // the node, and every mousemove re-enters here.
  if (_detailOpen || document.querySelector(".dag-inspector")) return;
  const tip = ensureTooltip(body);
  const id = String(node.id || "");

  if (id !== _currentId) {
    _currentId = id;
    tip.classList.remove("visible");
    if (_showTimer) window.clearTimeout(_showTimer);
    // 停留 SHOW_DELAY_MS 才出卡——扫过节点不弹窗。
    _showTimer = window.setTimeout(() => {
      if (_currentId !== id || !_tooltip) return;
      _tooltip.innerHTML = "";
      renderNodeInfo(_tooltip, node, el || null, false);
      _tooltip.classList.add("visible");
      _position(_tooltip, body, nodeRect);
      // 内容宽度在下一帧才量得准，再对一次位，防溢出右缘。
      requestAnimationFrame(() => {
        if (_currentId === id && _tooltip) _position(_tooltip, body, nodeRect);
      });
    }, SHOW_DELAY_MS);
  }
  // Repeated mousemoves over the same node only re-position an
  // already-visible card — they must not bypass the show delay.
  if (tip.classList.contains("visible")) _position(tip, body, nodeRect);
}

/** Expand THE card in place for ``node``: the detail rows plus the
 *  verb list ``menuEl`` (built by render/inspector.ts). If the brief
 *  state is already showing for this node it deepens where it stands —
 *  same window, more of it; otherwise the card appears where hover
 *  would have put it. The card turns interactive (`.detail` flips
 *  ``pointer-events``) and stays until ``closeTooltipDetail``. */
export function expandTooltip(
  node: GNode,
  el: Element,
  menuEl: HTMLElement,
): void {
  const body = document.querySelector(
    "#historyPanel .history-body",
  ) as HTMLElement | null;
  if (!body) return;
  const tip = ensureTooltip(body);
  if (_showTimer) {
    window.clearTimeout(_showTimer);
    _showTimer = 0;
  }
  _currentId = String(node.id || "");
  _detailOpen = true;
  tip.innerHTML = "";
  renderNodeInfo(tip, node, el, true);
  const sep = document.createElement("div");
  sep.className = "dag-menu-sep";
  tip.appendChild(sep);
  tip.appendChild(menuEl);
  tip.classList.add("detail", "visible");
  _position(tip, body, el.getBoundingClientRect());
  // 内容宽高在下一帧才量得准，再对一次位，防溢出视口。
  requestAnimationFrame(() => {
    if (_detailOpen && _tooltip) {
      _position(_tooltip, body, el.getBoundingClientRect());
    }
  });
}

// ── render ─────────────────────────────────────────────────────────

/** Render the node's info — header plus rows — into ``into``.
 *  ``detail: false`` is the hover cut (short previews, core rows);
 *  ``detail: true`` is the right-click cut (every field, long
 *  previews, coverage / context / id). Shared with the context menu
 *  so both surfaces read the same facts. */
export function renderNodeInfo(
  into: HTMLElement,
  node: GNode,
  el: Element | null,
  detail: boolean,
): void {
  _appendHeader(into, node, el);
  tooltipRows(node, el, detail).forEach((row) => {
    if (row.kind === "block") {
      _appendBlock(into, row.key, row.value, detail ? DETAIL_CHARS : BRIEF_CHARS);
    } else {
      _appendKv(into, row.key, row.value);
    }
  });
}

function _appendHeader(
  tip: HTMLElement,
  node: GNode,
  el: Element | null,
): void {
  const header = document.createElement("div");
  header.className = "history-tooltip-header";
  const title = document.createElement("div");
  title.className = "history-tooltip-kind";
  title.textContent = kindLabel(node, el);
  header.appendChild(title);
  const chips: string[] = [];
  if (node.source && node.source !== "web") chips.push(node.source);
  if (node.is_error) chips.push("error");
  if (chips.length) {
    const meta = document.createElement("div");
    meta.className = "history-tooltip-chips";
    chips.forEach((c) => {
      const chip = document.createElement("span");
      chip.className = "history-tooltip-chip";
      chip.textContent = c;
      meta.appendChild(chip);
    });
    header.appendChild(meta);
  }
  tip.appendChild(header);
}

function _appendKv(tip: HTMLElement, key: string, value: string): void {
  const row = document.createElement("div");
  row.className = "history-tooltip-kv";
  const ks = document.createElement("span");
  ks.className = "history-tooltip-kv-key";
  ks.textContent = key;
  const vs = document.createElement("span");
  vs.className = "history-tooltip-kv-val";
  vs.textContent = value;
  row.appendChild(ks);
  row.appendChild(vs);
  tip.appendChild(row);
}

function _appendBlock(
  tip: HTMLElement,
  key: string,
  value: string,
  chars: number,
): void {
  const wrap = document.createElement("div");
  wrap.className = "history-tooltip-block";
  const lbl = document.createElement("div");
  lbl.className = "history-tooltip-label";
  lbl.textContent = key;
  wrap.appendChild(lbl);
  const bod = document.createElement("div");
  bod.className = "history-tooltip-body";
  bod.textContent = clampText(value || "(empty)", chars);
  wrap.appendChild(bod);
  tip.appendChild(wrap);
}

// ── position ───────────────────────────────────────────────────────

/** Anchor the card BELOW the node, never overlapping it. The user
 *  is looking at the node when they hover — the card must not
 *  obscure that. Card is ``fixed`` so it can float across any
 *  region of the page.
 *
 *  Order of preference (each clamped to viewport):
 *    1. Below the node (default).
 *    2. Above the node (if below would clip the bottom of the viewport).
 *    3. Side fallback (if both above/below would clip). */
function _position(tip: HTMLElement, body: HTMLElement, nodeRect: DOMRect): void {
  void body;
  tip.style.position = "fixed";
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Vertical: below the node by default.
  let topPx = nodeRect.bottom + GAP;
  if (topPx + th > vh - 6) {
    const aboveTop = nodeRect.top - GAP - th;
    if (aboveTop >= 6) {
      topPx = aboveTop;
    } else {
      topPx = Math.max(6, vh - 6 - th);
    }
  }

  // Horizontal: center under the node, then clamp.
  let leftPx = nodeRect.left + nodeRect.width / 2 - tw / 2;
  if (leftPx + tw > vw - 6) leftPx = vw - 6 - tw;
  if (leftPx < 6) leftPx = 6;

  tip.style.left = leftPx + "px";
  tip.style.top = topPx + "px";
}
