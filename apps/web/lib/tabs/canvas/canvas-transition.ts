/** Animated canvas changes, the way macOS moves windows: a pane that stays
 *  slides and resizes from where it was, a pane that leaves fades under the
 *  panes that take its place, and a pane that arrives fades in.
 *
 *  Closing a pane (red, yellow, ⌘W) is not animated: the pane leaves the
 *  layout in the click's frame and its neighbours take the room at once,
 *  as a window closes. Only rearranging animates.
 *
 *  A caller captures the canvas just before it changes the layout; the app
 *  shell plays the capture in the layout effect of the commit that changed
 *  it, before that frame paints. Panes are matched by tab id, so a tab that
 *  moves between the split's peer pane and the lone chat shell still
 *  animates. Geometry runs through the Web Animations API, which never
 *  touches the panes' inline calc() geometry: when an animation ends the
 *  pane is simply back on its own style. */
import { setCanvasAnimating } from "./canvas-motion";

/** The side of the canvas a pane docked by a drop comes in from. */
export type CanvasEnter = "left" | "right" | "top" | "bottom";

const MOVE_MS = 300;
const EASE = "cubic-bezier(.2, .8, .2, 1)";
/** A capture the layout never followed is dropped rather than played on
 *  some later change. */
const STALE_MS = 1500;

type Ghost = { tabId: string; node: HTMLElement; rect: DOMRect; scroll: [number, number, number][] };
/** A zoom already under way elsewhere (the overview's card growing over
 *  the canvas) that the canvas takes over: the same curve out of `from`,
 *  on the same clock. `startTime` is the card animation's own (on the
 *  document timeline; the compositor that plays it sets it, so it holds
 *  even when the main thread saw the card late), null if not started. */
export type CanvasZoom = { from: DOMRect; startTime: number | null; duration: number; easing: string };
type Capture = { at: number; root: HTMLElement; rects: Map<string, DOMRect>; ghosts: Ghost[]; enters: Record<string, CanvasEnter>; zoom?: CanvasZoom };

let pending: Capture | null = null;
let settleTimer: ReturnType<typeof setTimeout> | undefined;

function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function canvasRoot(): HTMLElement | null {
  return document.querySelector<HTMLElement>("[data-canvas-root]");
}

/** The panes on screen, by tab id: the chat shell, peer and page panes,
 *  and file panes. */
function paneElements(root: HTMLElement): Map<string, HTMLElement> {
  const out = new Map<string, HTMLElement>();
  for (const el of root.querySelectorAll<HTMLElement>(":scope > [data-center-tab-id], :scope > [data-file-pane-id]")) {
    const id = el.dataset.centerTabId || el.dataset.filePaneId;
    if (!id || out.has(id)) continue;
    if (getComputedStyle(el).display === "none") continue;
    const box = el.getBoundingClientRect();
    if (box.width > 0 && box.height > 0) out.set(id, el);
  }
  return out;
}

/** A still copy of a pane: no ids or pane markers (nothing may find it as
 *  the real thing), inert, and scrolled where the pane was. */
function ghostOf(el: HTMLElement): { node: HTMLElement; scroll: [number, number, number][] } {
  const node = el.cloneNode(true) as HTMLElement;
  const scroll: [number, number, number][] = [];
  const from = el.querySelectorAll<HTMLElement>("*");
  const to = node.querySelectorAll<HTMLElement>("*");
  from.forEach((source, index) => {
    if (source.scrollTop || source.scrollLeft) scroll.push([index, source.scrollTop, source.scrollLeft]);
  });
  for (const item of [node, ...to]) {
    item.removeAttribute("id");
    item.removeAttribute("data-center-tab-id");
    item.removeAttribute("data-file-pane-id");
    item.removeAttribute("data-canvas-pane");
  }
  node.setAttribute("data-canvas-ghost", "");
  node.setAttribute("aria-hidden", "true");
  node.inert = true;
  return { node, scroll };
}

/** Record the canvas before a layout change. `enters` names tabs that
 *  arrive from a side of the canvas; a tab that leaves fades under the
 *  panes that take its place. */
export function captureCanvasTransition(enters: Record<string, CanvasEnter> = {}): void {
  if (typeof document === "undefined" || reducedMotion()) return;
  const root = canvasRoot();
  if (!root) return;
  const rects = new Map<string, DOMRect>();
  const ghosts: Ghost[] = [];
  for (const [id, el] of paneElements(root)) {
    const rect = el.getBoundingClientRect();
    rects.set(id, rect);
    const { node, scroll } = ghostOf(el);
    ghosts.push({ tabId: id, node, rect, scroll });
  }
  if (!rects.size) return;
  begin(root, rects, ghosts, enters);
}

/** Record a layout change seen from somewhere other than the live canvas:
 *  `rects` says where each staying pane starts, in the canvas's own
 *  unscaled frame, and the panes named in `enters` slide in from that
 *  side as the others make room, the edge between them moving as one.
 *  With `zoom`, the whole canvas meanwhile grows out of the overview card
 *  it replaces. Nothing leaves: whatever the canvas showed before is
 *  covered. */
export function captureCanvasLanding(rects: Map<string, DOMRect>, enters: Record<string, CanvasEnter>, zoom?: CanvasZoom): void {
  if (typeof document === "undefined" || reducedMotion()) return;
  const root = canvasRoot();
  if (root && rects.size) begin(root, rects, [], enters, zoom);
}

function begin(root: HTMLElement, rects: Map<string, DOMRect>, ghosts: Ghost[], enters: Record<string, CanvasEnter>, zoom?: CanvasZoom): void {
  pending = { at: performance.now(), root, rects, ghosts, enters, zoom };
  // Web panes start freezing to a snapshot now, so the native views are
  // still by the time the panes move.
  setCanvasAnimating(true);
  clearTimeout(settleTimer);
  settleTimer = setTimeout(() => {
    if (pending && performance.now() - pending.at >= STALE_MS) pending = null;
    if (!pending) setCanvasAnimating(false);
  }, STALE_MS);
}

function moved(a: DOMRect, b: DOMRect): boolean {
  return Math.abs(a.left - b.left) > 0.5 || Math.abs(a.top - b.top) > 0.5
    || Math.abs(a.width - b.width) > 0.5 || Math.abs(a.height - b.height) > 0.5;
}

function place(node: HTMLElement, rect: DOMRect, origin: DOMRect): void {
  Object.assign(node.style, {
    position: "absolute",
    left: `${rect.left - origin.left}px`,
    top: `${rect.top - origin.top}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
    margin: "0",
    zIndex: "0",
    pointerEvents: "none",
    transformOrigin: "0 0",
  });
}

/** Play the capture if this commit carried out its layout change. Called
 *  from the app shell's layout effect, so the first frame already shows
 *  the panes at their old places. */
export function playCanvasTransition(): void {
  const capture = pending;
  if (!capture) return;
  if (performance.now() - capture.at > STALE_MS || document.documentElement.hasAttribute("data-canvas-dragging")) {
    pending = null;
    return;
  }
  const root = capture.root.isConnected ? capture.root : canvasRoot();
  if (!root) { pending = null; return; }
  const now = paneElements(root);
  const moves: { el: HTMLElement; from: DOMRect; to: DOMRect }[] = [];
  const arrivals: HTMLElement[] = [];
  const entries: { el: HTMLElement; side: CanvasEnter }[] = [];
  let changed = false;
  for (const [id, el] of now) {
    const from = capture.rects.get(id);
    const side = capture.enters[id];
    if (!from && side) { entries.push({ el, side }); changed = true; continue; }
    if (!from) { arrivals.push(el); changed = true; continue; }
    const to = el.getBoundingClientRect();
    if (moved(from, to)) { moves.push({ el, from, to }); changed = true; }
  }
  const leaving = capture.ghosts.filter(ghost => !now.has(ghost.tabId));
  if (!changed && !leaving.length) return;
  pending = null;

  const origin = root.getBoundingClientRect();
  root.setAttribute("data-canvas-animating", "");
  const animations: Animation[] = [];
  for (const { el, from, to } of moves) {
    // Geometry, not a scaled snapshot: text re-wraps as the pane grows, and
    // chats re-wrap only the rows in view while the width is moving.
    animations.push(el.animate([
      { left: `${from.left - origin.left}px`, top: `${from.top - origin.top}px`, width: `${from.width}px`, height: `${from.height}px` },
      { left: `${to.left - origin.left}px`, top: `${to.top - origin.top}px`, width: `${to.width}px`, height: `${to.height}px` },
    ], { duration: MOVE_MS, easing: EASE }));
  }
  for (const { el, side } of entries) {
    // Laid out at its final size from the start, so nothing in it re-wraps:
    // it slides in from the canvas edge, clipped there, while the panes
    // beside it give up the room. Same timing as theirs, so the edge
    // between them stays one edge.
    const { width, height } = el.getBoundingClientRect();
    const [shift, clip] = side === "right" ? [`translateX(${width}px)`, `inset(0 ${width}px 0 0)`]
      : side === "left" ? [`translateX(${-width}px)`, `inset(0 0 0 ${width}px)`]
      : side === "bottom" ? [`translateY(${height}px)`, `inset(0 0 ${height}px 0)`]
      : [`translateY(${-height}px)`, `inset(${height}px 0 0 0)`];
    animations.push(el.animate([
      { transform: shift, clipPath: clip },
      { transform: "none", clipPath: "inset(0 0 0 0)" },
    ], { duration: MOVE_MS, easing: EASE }));
  }
  for (const el of arrivals) {
    animations.push(el.animate([
      { opacity: 0, transform: "scale(.97)" },
      { opacity: 1, transform: "none" },
    ], { duration: MOVE_MS, easing: EASE }));
  }
  for (const ghost of leaving) {
    // A pane hidden behind a growing one (green, Fill, an arrangement with
    // fewer panes) stays under the panes that take its place as it fades.
    const { node, rect } = ghost;
    place(node, rect, origin);
    root.prepend(node);
    const all = node.querySelectorAll<HTMLElement>("*");
    for (const [index, top, left] of ghost.scroll) {
      const item = all[index];
      if (item) { item.scrollTop = top; item.scrollLeft = left; }
    }
    const animation = node.animate([{ opacity: 1 }, { opacity: 0 }], { duration: MOVE_MS, easing: "ease-out", fill: "forwards" });
    animations.push(animation);
    void animation.finished.catch(() => undefined).then(() => node.remove());
  }
  // Last, once everything is measured unscaled: the canvas takes over the
  // card's zoom where it has got to.
  let zoomLeft = 0;
  if (capture.zoom) {
    const { from, startTime, duration, easing } = capture.zoom;
    const now = Number(document.timeline.currentTime ?? performance.now());
    zoomLeft = Math.max(0, duration - (startTime === null ? 0 : now - startTime));
    if (zoomLeft > 0 && origin.width > 0) {
      root.setAttribute("data-canvas-landing", "");
      const zoom = root.animate([
        { transformOrigin: "0 0", transform: `translate(${from.left - origin.left}px, ${from.top - origin.top}px) scale(${from.width / origin.width})` },
        { transformOrigin: "0 0", transform: "none" },
      ], { duration, easing });
      if (startTime !== null) zoom.startTime = startTime;
      void zoom.finished.catch(() => undefined).then(() => root.removeAttribute("data-canvas-landing"));
    }
  }
  clearTimeout(settleTimer);
  settleTimer = setTimeout(() => {
    root.removeAttribute("data-canvas-animating");
    setCanvasAnimating(false);
  }, Math.max(MOVE_MS, zoomLeft) + 20);
}
