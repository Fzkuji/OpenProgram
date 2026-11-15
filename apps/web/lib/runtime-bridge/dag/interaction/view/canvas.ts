/**
 * The infinite canvas the DAG is drawn on.
 *
 * The graph used to live in a scroll box sized to its content. That box
 * decided two things it had no business deciding: how big the graph was
 * allowed to be before it needed scrollbars, and where "the middle" was.
 * A wide session got a horizontal scrollbar, a deep one got a vertical
 * one, and reading the whole shape meant scrolling in two axes with no
 * way to zoom out.
 *
 * So there is no box. The SVG fills the pane, everything is drawn inside
 * one ``<g>``, and that group carries a translate + scale the user drives
 * directly — wheel, pinch and drags (``./gestures.ts``), zooming 25%–300%
 * about the cursor so what is under it stays under it.
 *
 * The pane's background paints a dot lattice at the layout's own grid
 * pitch, transformed with the same numbers, so the dots ARE the
 * coordinate system: a node visibly sits on one, and drifting off it is
 * a bug anyone can see without reading the layout code.
 *
 * View state lives in ``store/globals`` per session, so re-rendering the
 * graph — which happens on every capture — leaves the user where they
 * were looking. Only a session switch re-fits.
 */

import { animate, type AnimationPlaybackControls } from "framer-motion";
import { COL_W, COMPOSER_STRIP, PAD_X, PAD_Y } from "../../types";
import { TILE_R } from "../../render/tiles";
import { seatOf } from "../../render/seat";
import { closeNodeLayers } from "../node/inspector";
import { hideTooltip } from "../node/tooltip";
import { desktopBridge } from "@/lib/desktop/bridge/bridge-api";
import { WHEEL_ZOOM_RATE, wireGestures } from "./gestures";
import { hideMinimap, paintMinimap } from "./minimap";
import { paintFarLabels } from "./far-labels";
import {
  _viewSession,
  _viewScale,
  _viewTx,
  _viewTy,
  setView,
  setViewSession,
} from "../../store/globals";

/** The inspector popover and the hover card are anchored in SCREEN
 *  space, at where their node was when they opened. The camera moving
 *  under them leaves them floating over nothing — so any pan or zoom
 *  dismisses them, the same contract every canvas app honours. */
function dismissOverlays(): void {
  closeNodeLayers();
  hideTooltip();
}

const MIN_SCALE = 0.25;
const MAX_SCALE = 3;
/** Below this the tiles draw as solid blocks (``is-far``). */
const FAR_SCALE = 0.55;

/** One HUD −/+ press is exactly one standard wheel notch (deltaY =
 *  100), so the buttons and the wheel are the same control at the same
 *  pace. */
const ZOOM_STEP = Math.exp(100 * WHEEL_ZOOM_RATE);

/** Padding around the graph when fitting, in screen pixels. */
const FIT_PAD = 72;


interface CanvasHandle {
  world: SVGGElement;
  host: HTMLElement;
}

let _handle: CanvasHandle | null = null;

/** A session's opening fit stays owed until one actually lands. The
 *  first attach often happens before the pane has a size or the SVG a
 *  layout — ``fitCanvas`` bails on both — and a one-shot flag burned
 *  there would leave the graph parked at the origin (top-left) for the
 *  rest of the session. Re-renders and resizes retry while this is
 *  set; the first successful fit clears it. */
let _fitPending = false;
/** The frame the owed fit is booked on. A session opening re-renders
 *  several times in one task (coverage, branch names, unfolds); each
 *  attach asks for the fit, and one frame answers them all. */
let _fitFrame = 0;

/** What the last ``paintView`` wrote, so a pan — the common case, one
 *  call per pointer event — rewrites only the two translations and
 *  leaves the scale-bound work (CSS variables, label culling, lattice
 *  pitch) alone. Reset whenever a new graph is attached. */
let _painted: { world: SVGGElement | null; scale: number; dotR: string } = {
  world: null, scale: NaN, dotR: "",
};
let _viewFrame = 0;

/** Ask for the current view to be painted on the next frame. Gestures
 *  fire faster than the screen refreshes (a trackpad sends 120 wheel
 *  events a second); they all move the store, and one paint per frame
 *  shows where they ended up. */
export function applyView(): void {
  if (_viewFrame || !_handle) return;
  _viewFrame = requestAnimationFrame(() => {
    _viewFrame = 0;
    paintView();
  });
}

function placeWorld(world: SVGGElement): void {
  world.setAttribute("transform", `translate(${_viewTx},${_viewTy}) scale(${_viewScale})`);
}

/** Push the current view onto the world group, the backdrop lattice,
 *  the semantic-zoom variables, the zoom readout and the minimap — in
 *  one function, in one frame, so they can never disagree. Sizes are
 *  read before anything is written, so painting never forces a layout. */
function paintView(): void {
  if (!_handle) return;
  const { world, host } = _handle;
  const w = host.clientWidth;
  const h = host.clientHeight;
  const rescaled = _painted.scale !== _viewScale || _painted.world !== world;
  placeWorld(world);
  // The lattice pitch is the layout's own column width, and the offset
  // puts a dot's CENTRE under every node anchor: nodes sit at
  // ``PAD + k * COL_W`` in world space, while a background tile paints
  // its dot in the tile's middle — so the tile origin backs up half a
  // tile from the pad.
  //
  // Zoomed far out the on-screen pitch collapses and the pane dissolves
  // into solid dot noise — so the lattice COARSENS in powers of two,
  // keeping the pitch at 24px or more. Doubling the step keeps every
  // remaining dot on a node anchor (a 2^n multiple of COL_W still hits
  // the grid), so the lattice never drifts off the coordinate system it
  // is.
  const rawPitch = COL_W * _viewScale;
  const factor = rawPitch >= 24
    ? 1
    : Math.pow(2, Math.ceil(Math.log2(24 / rawPitch)));
  const step = COL_W * factor;
  const pitch = step * _viewScale;
  host.style.backgroundPosition =
    `${_viewTx + (PAD_X - step / 2) * _viewScale}px `
    + `${_viewTy + (PAD_Y - step / 2) * _viewScale}px`;
  if (rescaled) {
    host.style.backgroundSize = `${pitch}px ${pitch}px`;
    // The dot radius rides the zoom too: fixed at 1.2px it vanishes into
    // a 100px tile when zoomed in. Clamped so zoomed-out views don't
    // dissolve into noise. The colour is the theme's own hairline — a
    // hardcoded white was invisible on light themes.
    const dotR = Math.min(3, Math.max(1, 1.2 * _viewScale)).toFixed(2);
    if (dotR !== _painted.dotR) {
      host.style.backgroundImage =
        `radial-gradient(var(--border-light) `
        + `${dotR}px, transparent ${dotR}px)`;
    }
    // Semantic zoom (dag/rendering.md §4): zoomed out, the agents keep
    // their on-screen size like markers on a map, labels shrink only
    // gently (12px at 100%, about 8.5px at the least zoom), and below
    // FAR_SCALE the tiles turn into solid blocks of their tone, too
    // small for an icon. Each variable restyles the whole graph, so
    // they are written only when the scale moves.
    host.style.setProperty("--dag-marker-scale", String(Math.max(1, 1 / _viewScale)));
    host.style.setProperty("--dag-label-scale", String(Math.max(1, _viewScale ** -0.75)));
    host.classList.toggle("is-far", _viewScale < FAR_SCALE);
    paintFarLabels(world, _viewScale < 1);
    _painted = { world, scale: _viewScale, dotR };
  }
  // React draws the readout as "100%"; a remount with no zoom since
  // must still show the real figure, so it is checked every paint.
  const pct = document.querySelector<HTMLElement>(".dag-zoom-readout");
  const label = Math.round(_viewScale * 100) + "%";
  if (pct && pct.textContent !== label) pct.textContent = label;
  const seen = { tx: _viewTx, ty: _viewTy, scale: _viewScale, w, h: h - COMPOSER_STRIP };
  paintMinimap(host, world, seen, jumpTo);
}

/** Centre the view on world point (x, y), keeping the zoom (the minimap). */
function jumpTo(x: number, y: number): void {
  if (!_handle) return;
  _glide?.stop();
  _glideScale = null;
  const { host } = _handle;
  setView(Math.round(host.clientWidth / 2 - x * _viewScale),
    Math.round((host.clientHeight - COMPOSER_STRIP) / 2 - y * _viewScale), _viewScale);
  applyView();
}

/** The smallest scale a session OPENS at. A long session fitted whole
 *  shrank to a hairline column of unreadable specks (a 40-turn chat
 *  opened at 37%); arriving at a session should show where it is now,
 *  readable, and leave "everything at once" to the Fit button. */
const OPEN_MIN_SCALE = 0.8;

/** Centre the graph in the pane at a scale that shows all of it (or
 *  ``only`` that box of it, the selection's — ``frameSelection``).
 *
 *  ``opening`` is a session's first view: the same fit, but never below
 *  ``OPEN_MIN_SCALE``. When the graph does not fit at that scale the
 *  view frames HEAD instead — near the bottom of the strip, the way a
 *  transcript opens on its latest message.
 *
 *  The translation is rounded to whole pixels: at a fractional offset
 *  every node sits a fraction of a pixel off its background dot, and the
 *  lattice stops reading as the coordinate system it is. */
export function fitCanvas(opening = false, only?: DOMRect): void {
  _fitFrame = 0;
  if (!_handle) return;
  _glide?.stop();
  _glideScale = null;
  dismissOverlays();
  const { world, host } = _handle;
  const w = host.clientWidth;
  // The composer floats over the bottom of the pane. The canvas runs
  // under it — panning there is one gesture — but a FIT should land the
  // graph where it can be read, so it centres in the strip above.
  const h = host.clientHeight - COMPOSER_STRIP;
  if (!w || h <= 0) return;
  // The graph's box depends on the scale it is seen at: zoomed out, the
  // labels keep most of their screen size (``--dag-label-scale``) and so
  // grow in world units. So a whole-graph fit measures twice — once to
  // find the scale, then again with the labels sized for it.
  for (let pass = 0; pass < (only ? 1 : 2); pass++) {
    let bb: DOMRect;
    try {
      bb = only ?? (world as SVGGraphicsElement).getBBox();
    } catch {
      return;
    }
    if (!bb.width || !bb.height) return;
    const fit = Math.min(
      (w - FIT_PAD * 2) / bb.width,
      (h - FIT_PAD * 2) / bb.height,
    );
    // A few selected nodes may come closer than the whole graph would.
    const scale = Math.min(
      only ? 2 : 1.3, Math.max(opening ? OPEN_MIN_SCALE : MIN_SCALE, fit),
    );
    let tx = (w - bb.width * scale) / 2 - bb.x * scale;
    let ty = (h - bb.height * scale) / 2 - bb.y * scale;
    const head = opening && scale > fit ? headPoint(world) : null;
    if (head) {
      // Whichever axis overflows follows HEAD; the other stays centred.
      if (bb.width * scale > w - FIT_PAD * 2) tx = w / 2 - head.x * scale;
      if (bb.height * scale > h - FIT_PAD * 2) ty = h - FIT_PAD - head.y * scale;
    }
    setView(Math.round(tx), Math.round(ty), scale);
    paintView();
  }
  _fitPending = false;
}

/** HEAD's anchor in world space — the node drawer stamps ``data-head``
 *  on its glyph, and the glyph's group carries the node's translate. */
function headPoint(world: SVGGElement): { x: number; y: number } | null {
  return seatOf(world.querySelector("[data-head='1']")?.closest(".history-node"));
}

/** Zoom by ``factor`` about the screen-space anchor ``(px, py)``: the
 *  world point under the anchor stays under it before and after. At
 *  once — the wheel and pinch call it on every event of a gesture. */
function zoomAt(px: number, py: number, factor: number): void {
  _glide?.stop();
  _glideScale = null;
  const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, _viewScale * factor));
  setView(
    px - (px - _viewTx) * (next / _viewScale),
    py - (py - _viewTy) * (next / _viewScale),
    next,
  );
  applyView();
}

/** Where a running zoom glide is headed, so a second press while the
 *  first is still moving steps on from there, not from mid-flight. */
let _glideScale: number | null = null;

/** Glide the zoom to ``target`` about the anchor ``(px, py)``, the way
 *  ``zoomAt`` jumps there: the world point under the anchor stays put
 *  every frame, and the scale moves evenly in log space, so zooming in
 *  and out feel the same speed. A button press or key is one discrete
 *  step, so it animates; a gesture already moves continuously. */
function glideZoom(px: number, py: number, target: number): void {
  const to = Math.min(MAX_SCALE, Math.max(MIN_SCALE, target));
  const from = _viewScale;
  const wx = (px - _viewTx) / from;
  const wy = (py - _viewTy) / from;
  _glide?.stop();
  _glideScale = to;
  _glide = animate(0, 1, {
    duration: 0.22,
    ease: [0.22, 1, 0.36, 1],
    onUpdate: (k) => {
      const s = from * Math.pow(to / from, k);
      setView(px - wx * s, py - wy * s, s);
      paintView(); // framer-motion already calls this once a frame
    },
    onComplete: () => { _glideScale = null; },
  });
}

/** One HUD −/+ press: one wheel notch of zoom, glided. The buttons have
 *  no cursor position to anchor on, so they anchor on the pane's centre. */
export function zoomStep(dir: 1 | -1): void {
  if (!_handle) return;
  dismissOverlays();
  const { host } = _handle;
  const base = _glideScale ?? _viewScale;
  glideZoom(host.clientWidth / 2, host.clientHeight / 2,
    base * (dir > 0 ? ZOOM_STEP : 1 / ZOOM_STEP));
}

/** The HUD's zoom readout doubles as the reset: glide back to 100%,
 *  pane centre anchored, pan otherwise kept. */
export function resetZoom(): void {
  if (!_handle) return;
  dismissOverlays();
  const { host } = _handle;
  glideZoom(host.clientWidth / 2, host.clientHeight / 2, 1);
}

/** Where the camera can go to find someone: each working agent's tile,
 *  in the order the token layer drew them, else HEAD. World units. */
function agentSpots(world: SVGGElement): { x: number; y: number }[] {
  const spots: { x: number; y: number }[] = [];
  world.querySelectorAll(".dag-agents > .dag-agent-token:not(.is-idle)").forEach((t) => {
    // A dot is pinned to its tile's centre (render/agents.ts).
    const at = seatOf(t);
    if (at) spots.push(at);
  });
  if (!spots.length) {
    const head = headPoint(world);
    if (head) spots.push(head);
  }
  return spots;
}

/** Fit the view to the selected nodes, as design tools' Shift+2 does. */
export function frameSelection(ids: readonly string[]): void {
  if (!_handle || !ids.length) return;
  const want = new Set(ids);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  _handle.world.querySelectorAll(".history-node").forEach((g) => {
    if (!want.has(g.getAttribute("data-msg-id") || "")) return;
    const at = seatOf(g);
    if (!at) return;
    x0 = Math.min(x0, at.x - TILE_R); y0 = Math.min(y0, at.y - TILE_R);
    x1 = Math.max(x1, at.x + TILE_R); y1 = Math.max(y1, at.y + TILE_R);
  });
  if (x0 > x1) return;
  fitCanvas(false, new DOMRect(x0, y0, x1 - x0, y1 - y0));
}

let _focusTurn = 0;
let _glide: AnimationPlaybackControls | null = null;

/** Glide the camera to the next working agent (or HEAD), keeping the
 *  zoom. Pressed again it moves on to the next agent, so with several
 *  at work one key walks through them all. */
export function focusAgent(): void {
  if (!_handle) return;
  dismissOverlays();
  const { world, host } = _handle;
  const spots = agentSpots(world);
  if (!spots.length) return;
  const at = spots[_focusTurn++ % spots.length];
  const w = host.clientWidth;
  const h = host.clientHeight - COMPOSER_STRIP;
  const from = { tx: _viewTx, ty: _viewTy };
  const to = { tx: Math.round(w / 2 - at.x * _viewScale), ty: Math.round(h / 2 - at.y * _viewScale) };
  _glide?.stop();
  _glideScale = null;
  _glide = animate(0, 1, {
    duration: 0.32,
    ease: [0.22, 1, 0.36, 1],
    onUpdate: (k) => {
      setView(from.tx + (to.tx - from.tx) * k, from.ty + (to.ty - from.ty) * k, _viewScale);
      paintView(); // framer-motion already calls this once a frame
    },
  });
}

/**
 * Put ``svg`` (with ``world`` inside it) on screen in ``host`` and hand
 * back the view the user had.
 *
 * A re-render is not a reason to move the camera: the graph repaints on
 * every capture, and refitting each time would drag the view around
 * while the user is reading. Only arriving at a different session — a
 * different graph entirely — starts from a fit.
 */
export function attachCanvas(
  host: HTMLElement,
  svg: SVGElement,
  world: SVGGElement,
  sessionId: string | null,
): void {
  host.replaceChildren(svg);
  _handle = { world, host };
  // A pan takes the camera from a glide still in flight.
  wireGestures(host, {
    zoomAt,
    dismiss: dismissOverlays,
    moved: () => { _glide?.stop(); _glideScale = null; applyView(); },
    followHeldButton: (held, onCursor, onReleased) =>
      desktopBridge()?.pointerFollow?.start(held, onCursor, onReleased) ?? null,
  });
  wireFitRetryOnResize(host);
  const fresh = _viewSession !== sessionId;
  setViewSession(sessionId);
  if (fresh) _fitPending = true;
  // The new graph takes the view's transform now — without it, it would
  // flash at the origin for a frame. The rest (labels, minimap) waits
  // for the frame: reading the pane's size here, straight after the
  // swap, would force a layout of the whole new SVG on every capture.
  placeWorld(world);
  applyView();
  if (_fitPending) {
    // getBBox needs the element laid out; a frame later it is. If the
    // pane still has no size (hidden tab, mid-layout), the fit stays
    // pending and the next render or resize retries it.
    _fitFrame ||= requestAnimationFrame(() => fitCanvas(true));
  }
}

/** The pane can gain its size long after the first attach (panel
 *  animated open, tab made visible) with no re-render in between —
 *  retry the owed fit the moment it has room. */
function wireFitRetryOnResize(host: HTMLElement): void {
  const h = host as HTMLElement & { _dagFitResizeWired?: boolean };
  if (h._dagFitResizeWired || typeof ResizeObserver === "undefined") return;
  h._dagFitResizeWired = true;
  // No host check: ``fitCanvas`` already bails without a handle, and
  // when one exists the owed fit belongs to it — a resize on any wired
  // pane is room enough to retry, so the stricter ``_handle.host ===
  // host`` half-guard only ever dropped retries.
  new ResizeObserver(() => {
    if (_fitPending) fitCanvas(true);
  }).observe(host);
}

/** The canvas is gone (empty session, skeleton): forget the handle so a
 *  stale group never receives a transform. */
export function detachCanvas(): void {
  if (_handle) hideMinimap(_handle.host);
  _handle = null;
  cancelAnimationFrame(_viewFrame);
  _viewFrame = 0;
}
