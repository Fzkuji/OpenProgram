/** Animated canvas changes, the way macOS moves windows: a pane that stays
 *  slides and resizes from where it was, a pane that leaves plays out as a
 *  ghost (a copy of how it looked), and a pane that arrives fades in.
 *
 *  A caller captures the canvas just before it changes the layout; the app
 *  shell plays the capture in the layout effect of the commit that changed
 *  it, before that frame paints. Panes are matched by tab id, so a tab that
 *  moves between the split's peer pane and the lone chat shell still
 *  animates. Geometry runs through the Web Animations API, which never
 *  touches the panes' inline calc() geometry: when an animation ends the
 *  pane is simply back on its own style. */
import { setCanvasAnimating } from "./canvas-motion";

/** How a leaving pane goes: red closes it (it fades and sinks), yellow
 *  folds it up into its tab in the strip, anything else (a pane hidden by
 *  green or by Fill) fades under the panes that take its place. */
export type CanvasExit = "close" | "minimize" | "behind";
/** The side of the canvas a pane docked by a drop comes in from. */
export type CanvasEnter = "left" | "right" | "top" | "bottom";

const MOVE_MS = 300;
const CLOSE_MS = 180;
const MINIMIZE_MS = 600;
/** A minimize whose tab is not above the pane shrinks toward it instead. */
const SHRINK_MS = 340;
/** The pane beside a folding one waits this long before it fills the room,
 *  so the fold leads and the fill follows. */
const FILL_DELAY_MS = 90;
const TAB_FLASH_MS = 420;
/** A pane folded out of a split has no tab of its own yet: the tab grows
 *  out in the strip while the fold gets under way. The fold leads off just
 *  behind it and reaches the strip after the tab is fully out. */
const TAB_GROW_MS = 260;
const FOLD_AFTER_GROW_MS = 80;
/** How long a fold waits for the still image of its pane before the layout
 *  changes without one. */
const STILL_WAIT_MS = 160;
/** Rows of the still image drawn per slice of the fold, in CSS px. */
const FOLD_ROW = 2;
const MAX_STEP_MS = 34;
const EASE = "cubic-bezier(.2, .8, .2, 1)";
/** A capture the layout never followed (a close a save prompt cancelled)
 *  is dropped rather than played on some later change. */
const STALE_MS = 1500;

/** A full-resolution picture of a pane taken just before it folds away:
 *  the fold draws it row by row on one canvas, which costs a frame almost
 *  nothing however heavy the pane is. */
type Still = { image: HTMLImageElement; rect: DOMRect; at: number };
type Ghost = { tabId: string; exit: CanvasExit; node: HTMLElement; rect: DOMRect; scroll: [number, number, number][]; still?: Still };
/** A zoom already under way elsewhere (the overview's card growing over
 *  the canvas) that the canvas takes over: the same curve out of `from`,
 *  on the same clock. `startTime` is the card animation's own (on the
 *  document timeline; the compositor that plays it sets it, so it holds
 *  even when the main thread saw the card late), null if not started. */
export type CanvasZoom = { from: DOMRect; startTime: number | null; duration: number; easing: string };
type Capture = { at: number; root: HTMLElement; rects: Map<string, DOMRect>; ghosts: Ghost[]; exits: Record<string, CanvasExit>; enters: Record<string, CanvasEnter>; zoom?: CanvasZoom; tabBoxes: Map<Element, DOMRect> };

let pending: Capture | null = null;
const stills = new Map<string, Still>();
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

/** Take a still of a pane about to fold away, before its layout change.
 *  Null when there is nothing to wait for (no desktop shell to take it, no
 *  such pane, or Reduce Motion); otherwise it settles once the still is
 *  ready or after a short wait, whichever comes first. */
export function prepareCanvasFold(tabId: string): Promise<void> | null {
  if (typeof document === "undefined" || reducedMotion()) return null;
  const capture = window.openprogramDesktop?.captureRegion;
  const root = canvasRoot();
  const el = root ? paneElements(root).get(tabId) : undefined;
  if (!capture || !el) return null;
  const rect = el.getBoundingClientRect();
  const still = (async () => {
    const url = await capture({ x: rect.left, y: rect.top, width: rect.width, height: rect.height }, { full: true });
    if (!url) return;
    const image = new Image();
    image.src = url;
    await image.decode();
    stills.set(tabId, { image, rect, at: performance.now() });
  })().catch(() => undefined);
  return Promise.race([still, new Promise<void>(resolve => setTimeout(resolve, STILL_WAIT_MS))]);
}

/** Record the canvas before a layout change. `exits` says how particular
 *  tabs leave; any other tab that leaves fades under the rest. `enters`
 *  names tabs that arrive from a side of the canvas. */
export function captureCanvasTransition(exits: Record<string, CanvasExit> = {}, enters: Record<string, CanvasEnter> = {}): void {
  if (typeof document === "undefined" || reducedMotion()) return;
  const root = canvasRoot();
  if (!root) return;
  const rects = new Map<string, DOMRect>();
  const ghosts: Ghost[] = [];
  for (const [id, el] of paneElements(root)) {
    const rect = el.getBoundingClientRect();
    rects.set(id, rect);
    const { node, scroll } = ghostOf(el);
    const still = stills.get(id);
    stills.delete(id);
    const fresh = still && performance.now() - still.at < STALE_MS && !moved(still.rect, rect) ? still : undefined;
    ghosts.push({ tabId: id, exit: exits[id] ?? "behind", node, rect, scroll, still: fresh });
  }
  if (!rects.size) return;
  begin(root, rects, ghosts, exits, enters);
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
  if (root && rects.size) begin(root, rects, [], {}, enters, zoom);
}

function begin(root: HTMLElement, rects: Map<string, DOMRect>, ghosts: Ghost[], exits: Record<string, CanvasExit>, enters: Record<string, CanvasEnter>, zoom?: CanvasZoom): void {
  const tabBoxes = new Map<Element, DOMRect>();
  for (const box of document.querySelectorAll('[role="tablist"] > *')) tabBoxes.set(box, box.getBoundingClientRect());
  pending = { at: performance.now(), root, rects, ghosts, exits, enters, zoom, tabBoxes };
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

function place(node: HTMLElement, rect: DOMRect, origin: DOMRect, z: number): void {
  Object.assign(node.style, {
    position: "absolute",
    left: `${rect.left - origin.left}px`,
    top: `${rect.top - origin.top}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
    margin: "0",
    zIndex: String(z),
    pointerEvents: "none",
    transformOrigin: "0 0",
  });
}

/** The whole tab button a minimized pane folds into, if the strip shows
 *  it: the strip's own child, not the focus target inside it. */
function stripTab(tabId: string): HTMLElement | null {
  const target = document.querySelector<HTMLElement>(`[role="tab"][data-tab-id="${CSS.escape(tabId)}"]`);
  const tab = target?.closest<HTMLElement>('[role="tablist"] > *') ?? target;
  const rect = tab?.getBoundingClientRect();
  return tab && rect && rect.width > 0 && rect.height > 0 ? tab : null;
}

/** Bring in a tab that just appeared out of a split: it slides out from
 *  beside the tab it left and the tabs after it slide over to make room.
 *  Only transform and opacity move, so the compositor plays it smoothly
 *  even while the app is still busy laying out the change. */
function growTab(tab: HTMLElement, before: Map<Element, DOMRect>): void {
  const easing = "cubic-bezier(.2, .8, .2, 1)";
  const others = [...(tab.parentElement?.children ?? [])].filter(box => box !== tab) as HTMLElement[];
  const old = [...before.values()];
  // The strip kept its order apart from the new tab: match the rest in order.
  if (others.length === old.length) others.forEach((box, index) => {
    const dx = old[index].left - box.getBoundingClientRect().left;
    if (Math.abs(dx) > 0.5) box.animate([{ transform: `translateX(${dx}px)` }, { transform: "none" }], { duration: TAB_GROW_MS, easing });
  });
  const width = tab.getBoundingClientRect().width;
  tab.animate([
    { transform: `translateX(${-width * 0.45}px) scaleX(.55)`, transformOrigin: "0 50%", opacity: 0 },
    { transform: "none", transformOrigin: "0 50%", opacity: 1 },
  ], { duration: TAB_GROW_MS, easing });
}

/** Which way a closed pane leaves: away from the pane that grows into its
 *  room. Null when no pane takes its place (it then fades as before). */
function closingSide(rect: DOMRect, moves: { from: DOMRect; to: DOMRect }[]): CanvasEnter | null {
  for (const { from, to } of moves) {
    const covers = to.left < rect.right - 1 && to.right > rect.left + 1 && to.top < rect.bottom - 1 && to.bottom > rect.top + 1;
    if (!covers) continue;
    if (from.right <= rect.left + 1) return "right";
    if (from.left >= rect.right - 1) return "left";
    if (from.bottom <= rect.top + 1) return "bottom";
    if (from.top >= rect.bottom - 1) return "top";
  }
  return null;
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));
const smoothstep = (value: number) => value * value * (3 - 2 * value);
const easeInOut = (value: number) => value < 0.5 ? 4 * value ** 3 : 1 - (-2 * value + 2) ** 3 / 2;

/** The projective transform that maps a w×h box onto the quad p0 p1 p2 p3
 *  (top-left, top-right, bottom-right, bottom-left). Null when the quad has
 *  collapsed to a line. */
function quadMatrix(w: number, h: number, [x0, y0, x1, y1, x2, y2, x3, y3]: number[]): string | null {
  const dx1 = x1 - x2, dx2 = x3 - x2, dx3 = x0 - x1 + x2 - x3;
  const dy1 = y1 - y2, dy2 = y3 - y2, dy3 = y0 - y1 + y2 - y3;
  const det = dx1 * dy2 - dx2 * dy1;
  if (Math.abs(det) < 1e-6) return null;
  const g = (dx3 * dy2 - dx2 * dy3) / det;
  const k = (dx1 * dy3 - dx3 * dy1) / det;
  const a = x1 - x0 + g * x1, b = x3 - x0 + k * x3;
  const d = y1 - y0 + g * y1, e = y3 - y0 + k * y3;
  return `matrix3d(${a / w},${d / w},0,${g / w},${b / h},${e / h},0,${k / h},0,0,1,0,${x0},${y0},0,1)`;
}

/** The genie fold: the pane bends into a funnel whose neck is the tab's
 *  bottom edge. The edge nearest the tab pinches first, then the pane pours
 *  up through the neck, packing tighter as it nears it, and is gone at the
 *  tab's bottom edge, never drawn over the tab; the tab lights once as it
 *  takes the pane in. With a still of the pane the fold draws it in thin
 *  rows on one canvas; without one, copies of the pane are cut into bands,
 *  each mapped onto a quad. */
function genieFold(ghost: Ghost, origin: DOMRect, root: HTMLElement, tab: HTMLElement, target: DOMRect, delay: number): void {
  const { rect: pane } = ghost;
  const W = pane.width, H = pane.height;
  // In pane coordinates: the neck spans the tab, `reach` is the depth from
  // the neck to the pane's bottom edge, `travel` lifts that edge to the neck.
  const neck = target.bottom - pane.top;
  const neckLeft = target.left - pane.left, neckRight = target.right - pane.left;
  const reach = Math.max(1, H - neck), travel = H - neck;
  const edge = (y: number, t: number): [number, number, number] => {
    const pinch = smoothstep(clamp01(t / 0.45));
    const lift = easeInOut(clamp01((t - 0.14) / 0.86));
    const depth = y - neck - lift * travel;
    // Rows pack tighter as they near the neck.
    const Y = neck + (depth > 0 && depth < reach ? reach * (depth / reach) ** (1 + 0.75 * pinch) : depth);
    const below = clamp01((Y - neck) / reach);
    const open = 1 - pinch * (1 - (1 - Math.cos(Math.PI * below)) / 2);
    return [neckLeft + (0 - neckLeft) * open, neckRight + (W - neckRight) * open, Y];
  };
  const draw = ghost.still ? stillFold(ghost.still, pane, target, origin, root, edge) : bandFold(ghost, origin, root, neck, edge);
  // The fold's own clock: a frame advances it by at most MAX_STEP_MS, so a
  // busy main thread pauses the fold instead of skipping part of it.
  let elapsed = -delay, last: number | null = null;
  let flashed = false;
  function frame(now: number) {
    elapsed += last === null ? 0 : Math.min(now - last, MAX_STEP_MS);
    last = now;
    const t = clamp01(elapsed / MINIMIZE_MS);
    if (!draw.layer.isConnected) return;
    draw.paint(t, 1 - clamp01((t - 0.85) / 0.15));
    if (!flashed && t >= 0.72) {
      flashed = true;
      tab.animate([
        { backgroundColor: "color-mix(in srgb, var(--text-bright) 18%, transparent)" },
        { backgroundColor: getComputedStyle(tab).backgroundColor },
      ], { duration: TAB_FLASH_MS, easing: "ease-out" });
    }
    if (t < 1) requestAnimationFrame(frame);
    else draw.layer.remove();
  }
  requestAnimationFrame(frame);
}

type Edge = (y: number, t: number) => [number, number, number];
type Fold = { layer: HTMLElement; paint: (t: number, alpha: number) => void };

function ghostLayer<T extends HTMLElement>(layer: T): T {
  layer.setAttribute("data-canvas-ghost", "");
  layer.setAttribute("aria-hidden", "true");
  layer.inert = true;
  return layer;
}

/** The still drawn in rows of FOLD_ROW px, each row stretched between the
 *  funnel's edges at its height. The canvas starts at the neck, so nothing
 *  above it is ever drawn. */
function stillFold(still: Still, pane: DOMRect, target: DOMRect, origin: DOMRect, root: HTMLElement, edge: Edge): Fold {
  const left = Math.min(pane.left, target.left), right = Math.max(pane.right, target.right);
  const top = Math.min(pane.top, target.bottom);
  const box = new DOMRect(left, top, right - left, pane.bottom - top);
  const scale = window.devicePixelRatio || 1;
  const canvas = ghostLayer(document.createElement("canvas"));
  canvas.width = Math.round(box.width * scale);
  canvas.height = Math.round(box.height * scale);
  place(canvas, box, origin, 50);
  root.append(canvas);
  const context = canvas.getContext("2d");
  const { image } = still;
  const sy = image.naturalHeight / pane.height;
  const ox = pane.left - left, oy = pane.top - top;
  const paint = (t: number, alpha: number) => {
    if (!context) return;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, box.width, box.height);
    context.globalAlpha = alpha;
    context.imageSmoothingQuality = "high";
    let previous = edge(0, t);
    for (let y0 = 0; y0 < pane.height; y0 += FOLD_ROW) {
      const y1 = Math.min(pane.height, y0 + FOLD_ROW);
      const next = edge(y1, t);
      const [l0, r0, top0] = previous, [l1, r1, top1] = next;
      previous = next;
      const height = top1 - top0;
      if (top1 + oy <= 0 || height < 0.01) continue;
      const l = (l0 + l1) / 2, r = (r0 + r1) / 2;
      // A hair of overlap so neighbouring rows never leave a seam.
      context.drawImage(image, 0, y0 * sy, image.naturalWidth, (y1 - y0) * sy,
        l + ox, top0 + oy, r - l, height + (height > 0.5 ? 0.5 : 0));
    }
  };
  paint(0, 1);
  return { layer: canvas, paint };
}

/** Without a still: copies of the pane cut into bands, each mapped every
 *  frame onto its quad of the funnel. */
function bandFold(ghost: Ghost, origin: DOMRect, root: HTMLElement, neck: number, edge: Edge): Fold {
  const { node, rect: pane } = ghost;
  const W = pane.width, H = pane.height;
  // Each band holds its own copy of the pane, so a heavy pane gets fewer.
  const nodes = node.getElementsByTagName("*").length + 1;
  const count = Math.max(6, Math.min(20, Math.round(36_000 / nodes)));
  const layer = ghostLayer(document.createElement("div"));
  place(layer, pane, origin, 50);
  layer.style.clipPath = `polygon(-100vw ${neck}px, 200vw ${neck}px, 200vw 200vh, -100vw 200vh)`;
  const bands: { band: HTMLElement; top: number; bottom: number }[] = [];
  for (let index = 0; index < count; index++) {
    const top = Math.round(index * H / count), bottom = Math.round((index + 1) * H / count);
    const band = document.createElement("div");
    Object.assign(band.style, {
      position: "absolute", left: "0", top: `${top}px`, width: `${W}px`, height: `${bottom - top + 1}px`,
      overflow: "hidden", transformOrigin: "0 0", willChange: "transform, opacity",
    });
    const copy = index === count - 1 ? node : node.cloneNode(true) as HTMLElement;
    Object.assign(copy.style, { position: "absolute", left: "0", top: `${-top}px`, width: `${W}px`, height: `${H}px`, margin: "0" });
    band.append(copy);
    layer.append(band);
    bands.push({ band, top, bottom });
  }
  root.append(layer);
  for (const { band } of bands) {
    const all = band.firstElementChild!.querySelectorAll<HTMLElement>("*");
    for (const [index, top, left] of ghost.scroll) {
      const item = all[index];
      if (item) { item.scrollTop = top; item.scrollLeft = left; }
    }
  }
  const paint = (t: number, alpha: number) => {
    for (const { band, top, bottom } of bands) {
      const [l0, r0, y0] = edge(top, t), [l1, r1, y1] = edge(bottom, t);
      const matrix = quadMatrix(W, bottom - top + 1, [l0, y0 - top, r0, y0 - top, r1, y1 - top, l1, y1 - top]);
      band.style.visibility = matrix && y1 - y0 > 0.25 ? "" : "hidden";
      if (matrix) band.style.transform = matrix;
      band.style.opacity = String(alpha);
    }
  };
  return { layer, paint };
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
  // A tab named to close or fold away is still here: its change has not
  // committed yet (a save prompt may be up). Wait for the commit that does.
  for (const id of Object.keys(capture.exits)) if (now.has(id)) return;
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
  // A pane folds into its tab when the tab sits above it; otherwise it
  // shrinks toward wherever the tab is.
  const folds = new Map<Ghost, { tab: HTMLElement; target: DOMRect; arrives: boolean }>();
  for (const ghost of leaving) {
    const tab = ghost.exit === "minimize" ? stripTab(ghost.tabId) : null;
    // Measured before the tab grows: the fold aims at where it ends up.
    const target = tab?.getBoundingClientRect();
    if (tab && target && target.bottom <= ghost.rect.top + 1) folds.set(ghost, { tab, target, arrives: !capture.tabBoxes.has(tab) });
  }
  const foldDelay = [...folds.values()].some(fold => fold.arrives) ? FOLD_AFTER_GROW_MS : 0;
  const animations: Animation[] = [];
  for (const { el, from, to } of moves) {
    // Geometry, not a scaled snapshot: text re-wraps as the pane grows, and
    // chats re-wrap only the rows in view while the width is moving.
    animations.push(el.animate([
      { left: `${from.left - origin.left}px`, top: `${from.top - origin.top}px`, width: `${from.width}px`, height: `${from.height}px` },
      { left: `${to.left - origin.left}px`, top: `${to.top - origin.top}px`, width: `${to.width}px`, height: `${to.height}px` },
    ], folds.size
      ? { duration: MINIMIZE_MS - FILL_DELAY_MS * 1.5, delay: foldDelay + FILL_DELAY_MS, easing: "cubic-bezier(.22, 1, .36, 1)", fill: "backwards" }
      : { duration: MOVE_MS, easing: EASE }));
  }
  for (const [ghost, { tab, target, arrives }] of folds) {
    if (arrives) growTab(tab, capture.tabBoxes);
    genieFold(ghost, origin, root, tab, target, foldDelay);
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
    if (folds.has(ghost)) continue;
    const { node, rect, exit } = ghost;
    // Red and yellow ghosts sit over the panes that move into their place;
    // a pane hidden behind a growing one stays under it.
    place(node, rect, origin, exit === "behind" ? 0 : 50);
    if (exit === "behind") root.prepend(node); else root.append(node);
    const all = node.querySelectorAll<HTMLElement>("*");
    for (const [index, top, left] of ghost.scroll) {
      const item = all[index];
      if (item) { item.scrollTop = top; item.scrollLeft = left; }
    }
    let animation: Animation;
    const side = exit === "close" ? closingSide(rect, moves) : null;
    if (side) {
      // The pane beside it takes the room: the closed pane slides out at
      // the canvas edge, clipped there, on the same clock as the growing
      // pane, so the edge between them moves as one. The mirror of a dock.
      const [shift, clip] = side === "right" ? [`translateX(${rect.width}px)`, `inset(0 ${rect.width}px 0 0)`]
        : side === "left" ? [`translateX(${-rect.width}px)`, `inset(0 0 0 ${rect.width}px)`]
        : side === "bottom" ? [`translateY(${rect.height}px)`, `inset(0 0 ${rect.height}px 0)`]
        : [`translateY(${-rect.height}px)`, `inset(${rect.height}px 0 0 0)`];
      animation = node.animate([
        { transform: "none", clipPath: "inset(0 0 0 0)" },
        { transform: shift, clipPath: clip },
      ], { duration: MOVE_MS, easing: EASE, fill: "forwards" });
    } else if (exit === "close") {
      animation = node.animate([
        { opacity: 1, transform: "none" },
        { opacity: 0, transform: `translate(${rect.width * 0.03}px, ${rect.height * 0.03}px) scale(.94)` },
      ], { duration: CLOSE_MS, easing: "cubic-bezier(.4, 0, 1, 1)", fill: "forwards" });
    } else if (exit === "minimize") {
      // No tab above the pane to fold into: shrink toward the tab, top edge
      // first.
      const tab = stripTab(ghost.tabId)?.getBoundingClientRect() ?? null;
      const target = tab ?? new DOMRect(rect.left + rect.width / 2 - 60, origin.top - 30, 120, 28);
      const dx = target.left - rect.left;
      const dy = target.top - rect.top;
      const sx = Math.max(0.02, target.width / rect.width);
      const sy = Math.max(0.02, target.height / rect.height);
      animation = node.animate([
        { opacity: 1, transform: "none", borderRadius: "16px" },
        { opacity: 0.9, transform: `translate(${dx * 0.35}px, ${dy * 0.55}px) scale(${0.35 + sx * 0.65}, ${0.3 + sy * 0.7})`, offset: 0.6 },
        { opacity: 0, transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`, borderRadius: "8px" },
      ], { duration: SHRINK_MS, easing: "cubic-bezier(.5, 0, .2, 1)", fill: "forwards" });
    } else {
      animation = node.animate([{ opacity: 1 }, { opacity: 0 }], { duration: MOVE_MS, easing: "ease-out", fill: "forwards" });
    }
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
  const longest = Math.max(MOVE_MS, zoomLeft, ...leaving.map(g => folds.has(g) ? foldDelay + MINIMIZE_MS : g.exit === "minimize" ? SHRINK_MS : g.exit === "close" && !closingSide(g.rect, moves) ? CLOSE_MS : MOVE_MS));
  settleTimer = setTimeout(() => {
    root.removeAttribute("data-canvas-animating");
    setCanvasAnimating(false);
  }, longest + 20);
}
