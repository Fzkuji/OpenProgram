/**
 * Node selection (dag/rendering.md §13).
 *
 * The canvas has one mouse, no tools to switch between. The left
 * button selects, the right one moves the camera:
 *
 *   * click a node            — select it, then its own action (fold, card);
 *   * Shift/⌘ + click         — add the node to the selection or take it out;
 *   * drag on empty canvas    — a box; every node it touches is selected
 *                               (with Shift/⌘, added to the selection);
 *   * click on empty canvas   — clear the selection;
 *   * right drag              — pan (a right click without a drag is the
 *                               node's menu).
 *
 * The selection is view state of the current session's canvas: it
 * holds node ids that are on screen, is pruned to what a repaint still
 * draws, and is dropped on a session switch.
 */

import { runtimeState } from "../../../state";
import { repaintMinimap } from "../view/minimap";
import { _svg } from "../../render/shapes";

let _selected: string[] = [];
let _selectionSession: string | null = null;
let _version = 0;
const _listeners = new Set<() => void>();

/** Tell the selection bar something it shows changed (also used by
 *  ``./node-delete`` for the delete/undo state). */
export function emit(): void {
  _version++;
  _listeners.forEach((fn) => fn());
}

/** React's ``useSyncExternalStore`` pair: any selection change
 *  bumps the version. */
export function subscribeSelection(fn: () => void): () => void {
  _listeners.add(fn);
  return () => {
    _listeners.delete(fn);
  };
}
export function selectionVersion(): number {
  return _version;
}

export function selectedIds(): readonly string[] {
  return _selected;
}

export type SelectMode = "replace" | "add" | "toggle";

export function select(ids: string[], mode: SelectMode = "replace"): void {
  let next: string[];
  if (mode === "replace") {
    next = Array.from(new Set(ids));
  } else if (mode === "add") {
    next = Array.from(new Set([..._selected, ...ids]));
  } else {
    const cur = new Set(_selected);
    ids.forEach((id) => (cur.has(id) ? cur.delete(id) : cur.add(id)));
    next = Array.from(cur);
  }
  _selectionSession = runtimeState.currentSessionId;
  if (next.length === _selected.length && next.every((id, i) => id === _selected[i])) {
    return;
  }
  _selected = next;
  paintSelection();
  emit();
}

export function clearSelection(): void {
  if (!_selected.length) return;
  _selected = [];
  paintSelection();
  emit();
}

function canvasHost(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector<HTMLElement>("#historyPanel .history-body");
}

/** Light every selected node and drop ids the canvas no longer draws
 *  (deleted, folded away, another session). Called after each repaint
 *  and on every selection change. */
export function paintSelection(): void {
  const host = canvasHost();
  if (!host) return;
  const sid = runtimeState.currentSessionId;
  let pruned = _selectionSession !== sid && _selected.length > 0;
  if (pruned) _selected = [];
  _selectionSession = sid;
  const want = new Set(_selected);
  const drawn = new Set<string>();
  host.querySelectorAll<SVGGElement>(".history-node").forEach((g) => {
    const id = g.getAttribute("data-msg-id") || "";
    const on = want.has(id);
    if (on) drawn.add(id);
    // Selected, the chip itself lights up (dag/graph/nodes.css): no frame.
    g.classList.toggle("is-selected", on);
  });
  // Zoomed far out, the rest of the graph fades behind a selection.
  host.classList.toggle("has-selection", drawn.size > 0);
  repaintMinimap();
  if (drawn.size !== want.size) {
    _selected = _selected.filter((id) => drawn.has(id));
    pruned = true;
  }
  if (pruned) emit();
}

/* ── hit-testing for the box (screen space) ──────────────────────── */

export interface Pt {
  x: number;
  y: number;
}

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The box touches a node, not only encloses it. */
export function rectTouchesBox(r: Box, b: Box): boolean {
  return r.left <= b.right && b.left <= r.right
    && r.top <= b.bottom && b.top <= r.bottom;
}

/** Ids of the drawn nodes whose glyph ``rect`` touches. ``rect`` is in
 *  viewport (client) coordinates. */
function nodesTouched(rect: Box): string[] {
  const host = canvasHost();
  if (!host) return [];
  const out: string[] = [];
  host.querySelectorAll<SVGGElement>(".history-node").forEach((g) => {
    const id = g.getAttribute("data-msg-id");
    const glyph = g.querySelector("[data-node-shape]") || g;
    if (!id) return;
    const r = glyph.getBoundingClientRect();
    if (!r.width && !r.height) return;
    if (rectTouchesBox(rect, r)) out.push(id);
  });
  return out;
}

/* ── the drag overlay ───────────────────────────────────────────── */

/** The box drawn while a left drag on empty canvas is under way. An SVG laid over the
 *  canvas in host coordinates; it never carries the camera transform,
 *  because it is a gesture, not part of the graph. */
export class SelectionBand {
  private host: HTMLElement;
  private origin: Pt;
  private end: Pt;
  private svg: SVGSVGElement;
  private shape: SVGElement;

  constructor(host: HTMLElement, origin: Pt) {
    this.host = host;
    this.origin = origin;
    this.end = origin;
    this.svg = _svg("svg", { class: "dag-select-band" }) as SVGSVGElement;
    this.shape = _svg("rect", {});
    this.svg.appendChild(this.shape);
    host.appendChild(this.svg);
  }

  move(p: Pt): void {
    this.end = p;
    const r = this.host.getBoundingClientRect();
    const a = this.origin;
    this.shape.setAttribute("x", String(Math.min(a.x, p.x) - r.left));
    this.shape.setAttribute("y", String(Math.min(a.y, p.y) - r.top));
    this.shape.setAttribute("width", String(Math.abs(a.x - p.x)));
    this.shape.setAttribute("height", String(Math.abs(a.y - p.y)));
  }

  /** What the box touched, in client coordinates. */
  hits(): string[] {
    const a = this.origin;
    const b = this.end;
    return nodesTouched({
      left: Math.min(a.x, b.x),
      top: Math.min(a.y, b.y),
      right: Math.max(a.x, b.x),
      bottom: Math.max(a.y, b.y),
    });
  }

  remove(): void {
    this.svg.remove();
  }
}

/* ── keyboard focus helpers ─────────────────────────────────────── */

export function isEditableFocus(): boolean {
  if (typeof document === "undefined") return false;
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** Selecting on the canvas takes the keyboard away from the composer,
 *  so Delete and Esc reach the selection instead of the draft. */
export function releaseEditableFocus(): void {
  if (isEditableFocus()) (document.activeElement as HTMLElement).blur();
}

/** The graph perspective is on screen. */
export function dagVisible(): boolean {
  const host = canvasHost();
  return !!host && host.getClientRects().length > 0;
}
