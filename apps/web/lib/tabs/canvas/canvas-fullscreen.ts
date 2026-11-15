/**
 * The center canvas's full screen: the session pane — the conversation or
 * its context graph, with the composer — takes the whole window over the
 * sidebars, the center tab strip and the canvas's other panes. A mode of
 * the app, not the browser's Fullscreen API: the window keeps its size.
 * The session pane's view controls flip it (chat/pane/view-controls.tsx);
 * they float over both perspectives, so switching between them keeps it.
 * Esc, a non-session tab becoming active or a reload ends it.
 *
 * The look is CSS keyed on ``html[data-canvas-fullscreen]``
 * (app/styles/chat/pane/center-pane.css): the center body goes fixed,
 * ``--canvas-fullscreen-inset`` inside the window, over the sidebars and
 * tab strip, which stay put. Native web views sit above every page element,
 * so while it is on only the floating preview's page, which floats over the
 * session pane, may show (desktop/bridge/bridge-state.ts listens for
 * ``CANVAS_FULLSCREEN_EVENT``).
 *
 * The glide moves the body's real edges, as the canvas moves its panes
 * (canvas-transition.ts) and as macOS zooms a window: the four edges travel
 * between the session pane's box and the window's inset, and the content
 * lays out at the box's size on every frame. It never lays the content out
 * at the full-screen size before the glide reaches it: the transcript's
 * scroll would be clamped to that shorter text and jump. So the end box is
 * named (the CSS variable), not measured, and the start box is also pinned
 * inline, since Chromium lays a new animation's first frame out at the
 * element's own style. While it runs the canvas counts as moving
 * (canvas-motion.ts), so the chat re-wraps only the rows in view and holds
 * its reader's line. Web Animations, not CSS transitions: the clock starts
 * on the first painted frame, so the heavy switch frame does not eat the
 * start of the curve. A toggle mid-glide turns it around; Reduce Motion
 * skips it.
 */

import { setCanvasAnimating } from "./canvas-motion";

/** A window-sized move: longer than the canvas's 300ms pane moves
 *  (canvas-transition.ts), and the way back a little quicker, as an
 *  element returning to its place goes. */
const ENTER_MS = 400;
const LEAVE_MS = 320;
/** Ease in and out alike, as macOS zooms a window. The content stops
 *  growing well before the window does (the chat column's max width), so
 *  it moves only in the first part of the way in and the last part of the
 *  way back: a front-loaded curve (Material's emphasized) ran that part in
 *  60ms going in, a jump, against 300ms coming back. Symmetric, it takes
 *  about 180ms both ways. */
const EASE = "cubic-bezier(0.65, 0, 0.35, 1)";
/** Fired on window once the attribute is set or, after the glide back, removed. */
export const CANVAS_FULLSCREEN_EVENT = "op:canvas-fullscreen";

let _on = false;
const _subs = new Set<() => void>();
/** The glide under way, and what to do when it ends. */
let _glide: Animation | null = null;
let _land: (() => void) | null = null;
/** Where the session pane sat in its slot when full screen began, for the
 *  way back: the slot's size, and the pane's box within it. */
let _home: { slot: DOMRect; pane: DOMRect } | null = null;

export function canvasFullscreen(): boolean {
  return _on;
}

/** Whether the page covers the window — true from the moment full screen
 *  starts until its glide back has ended. */
export function canvasCoversWindow(): boolean {
  return typeof document !== "undefined" && !!document.documentElement?.hasAttribute("data-canvas-fullscreen");
}

function announce(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CANVAS_FULLSCREEN_EVENT));
}

function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Where the center body sits in the layout: its column, under the tab
 *  strip when the strip lives in the column (web; the desktop App's
 *  strip is window chrome above it). */
function slot(body: HTMLElement): DOMRect {
  const col = body.parentElement!.getBoundingClientRect();
  const strip = body.parentElement!.querySelector<HTMLElement>(":scope > [data-center-tab-strip]");
  const top = strip ? strip.getBoundingClientRect().bottom : col.top;
  return new DOMRect(col.left, top, col.width, col.bottom - top);
}

/** The pane's box in the layout when the body sits in `inSlot`: where it
 *  was when full screen began, moved with the slot; the whole slot if the
 *  slot changed size since (a window resize). */
function homeOf(inSlot: DOMRect): DOMRect {
  const h = _home;
  if (!h || Math.abs(h.slot.width - inSlot.width) > 1 || Math.abs(h.slot.height - inSlot.height) > 1) return inSlot;
  return new DOMRect(h.pane.left - h.slot.left + inSlot.left, h.pane.top - h.slot.top + inSlot.top, h.pane.width, h.pane.height);
}

const EDGES = ["top", "left", "right", "bottom"] as const;
type Box = Record<(typeof EDGES)[number], string>;

/** The box as the fixed body's four insets from the window's edges. */
function box(r: DOMRect): Box {
  const root = document.documentElement;
  return {
    top: `${r.top}px`, left: `${r.left}px`,
    right: `${root.clientWidth - r.right}px`, bottom: `${root.clientHeight - r.bottom}px`,
  };
}

/** Where the stylesheet puts the full-screen body, named rather than
 *  measured (see the header). */
const INSET = "var(--canvas-fullscreen-inset)";
const FULL: Box = { top: INSET, left: INSET, right: INSET, bottom: INSET };

/** Hold the body at `at` through its own style; null lets go. */
function pin(body: HTMLElement, at: Box | null): void {
  for (const edge of EDGES) body.style[edge] = at ? at[edge] : "";
}

function play(body: HTMLElement, from: Box, to: Box, ms: number, land: () => void): void {
  const mine = body.animate([from, to], { duration: ms, easing: EASE, fill: "forwards" });
  _glide = mine;
  _land = land;
  setCanvasAnimating(true);
  mine.onfinish = () => {
    if (_glide !== mine) return;
    const done = _land;
    _glide = null;
    _land = null;
    done?.();
    setCanvasAnimating(false);
    pin(body, null);
    mine.cancel();
  };
}

export function setCanvasFullscreen(on: boolean): void {
  if (on === _on) return;
  _on = on;
  if (typeof document !== "undefined") {
    const root = document.documentElement;
    const body = document.querySelector<HTMLElement>(".center-col .center-body");
    const pane = body?.querySelector<HTMLElement>(":scope > .center-pane-chat") ?? null;
    const dropBack = () => {
      // Focusing the composer while the body is lifted out scrolls its
      // column (overflow: hidden, so nothing shows it) to "reveal" it;
      // the body would drop back into the layout shifted by that much.
      if (body) { body.parentElement!.scrollLeft = 0; body.parentElement!.scrollTop = 0; }
      root.removeAttribute("data-canvas-fullscreen");
      announce();
    };
    if (_glide) {
      // Mid-glide: turn it around from where it is.
      _glide.reverse();
      _land = on ? null : dropBack;
    } else if (!body || reducedMotion()) {
      if (on) { root.setAttribute("data-canvas-fullscreen", ""); announce(); } else dropBack();
    } else if (on) {
      // From the session pane's own box out to the window's inset.
      const inSlot = slot(body);
      const paneBox = pane?.getBoundingClientRect() ?? inSlot;
      _home = { slot: inSlot, pane: paneBox };
      const from = box(paneBox);
      pin(body, from);
      root.setAttribute("data-canvas-fullscreen", "");
      play(body, from, FULL, ENTER_MS, () => {});
      // Listeners may measure: only once the glide holds the body at its start.
      announce();
    } else {
      // Back onto the pane's own box, then into the layout there.
      play(body, FULL, box(homeOf(slot(body))), LEAVE_MS, dropBack);
    }
  }
  _subs.forEach((fn) => fn());
}
export function subscribeCanvasFullscreen(fn: () => void): () => void {
  _subs.add(fn);
  return () => { _subs.delete(fn); };
}

/** Esc leaves full screen from either perspective, the composer included,
 *  unless something else took the key first: the graph's selection
 *  (keys.ts), a find bar, or an open menu or dialog, which closes on the
 *  same press. Bubble phase on window, so every handler below runs first. */
function onEscape(e: KeyboardEvent): void {
  if (!_on || e.key !== "Escape" || e.defaultPrevented || e.isComposing) return;
  if (document.querySelector('[role="menu"], [role="dialog"], [role="listbox"], [data-radix-popper-content-wrapper]')) return;
  e.preventDefault();
  setCanvasFullscreen(false);
}
if (typeof window !== "undefined") window.addEventListener("keydown", onEscape);
