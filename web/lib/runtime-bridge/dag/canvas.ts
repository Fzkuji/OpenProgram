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
 * directly:
 *
 *   * wheel / two-finger swipe → pan
 *   * pinch, or ⌘/ctrl + wheel → zoom about the pointer, 25%–300%
 *   * drag on empty space → pan (a drag that starts on a node is the
 *     node's, so clicking still works)
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

import { COL_W, PAD_X, PAD_Y } from "./types";
import { closeNodeLayers } from "./render/inspector";
import { hideTooltip } from "./tooltip";
import {
  _viewSession,
  _viewScale,
  _viewTx,
  _viewTy,
  setView,
  setViewSession,
} from "./store/globals";

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

/** Padding around the graph when fitting, in screen pixels. */
const FIT_PAD = 72;

/** Height the floating composer and the HUD occupy at the bottom of the
 *  pane. The canvas runs underneath them; only ``fitCanvas`` cares,
 *  because a fit that centres behind the composer is a fit you have to
 *  undo by hand. */
const COMPOSER_STRIP = 150;

interface CanvasHandle {
  world: SVGGElement;
  host: HTMLElement;
}

let _handle: CanvasHandle | null = null;

/** Push the current view onto the world group, the backdrop lattice and
 *  the zoom readout. One function so the three can never disagree. */
export function applyView(): void {
  if (!_handle) return;
  const { world, host } = _handle;
  world.setAttribute(
    "transform",
    `translate(${_viewTx},${_viewTy}) scale(${_viewScale})`,
  );
  // The lattice pitch is the layout's own column width, and the offset
  // puts a dot's CENTRE under every node anchor: nodes sit at
  // ``PAD + k * COL_W`` in world space, while a background tile paints
  // its dot in the tile's middle — so the tile origin backs up half a
  // tile from the pad.
  const pitch = COL_W * _viewScale;
  host.style.backgroundPosition =
    `${_viewTx + (PAD_X - COL_W / 2) * _viewScale}px `
    + `${_viewTy + (PAD_Y - COL_W / 2) * _viewScale}px`;
  host.style.backgroundSize = `${pitch}px ${pitch}px`;
  const pct = document.querySelector<HTMLElement>(".dag-hud-zoom");
  if (pct) pct.textContent = Math.round(_viewScale * 100) + "%";
}

/** Centre the graph in the pane at a scale that shows all of it.
 *
 *  The translation is rounded to whole pixels: at a fractional offset
 *  every node sits a fraction of a pixel off its background dot, and the
 *  lattice stops reading as the coordinate system it is. */
export function fitCanvas(): void {
  if (!_handle) return;
  dismissOverlays();
  const { world, host } = _handle;
  let bb: DOMRect;
  try {
    bb = (world as SVGGraphicsElement).getBBox();
  } catch {
    return;
  }
  if (!bb.width || !bb.height) return;
  const w = host.clientWidth;
  // The composer floats over the bottom of the pane. The canvas runs
  // under it — panning there is one gesture — but a FIT should land the
  // graph where it can be read, so it centres in the strip above.
  const h = host.clientHeight - COMPOSER_STRIP;
  if (!w || h <= 0) return;
  const scale = Math.min(
    1.3,
    Math.max(
      MIN_SCALE,
      Math.min((w - FIT_PAD * 2) / bb.width, (h - FIT_PAD * 2) / bb.height),
    ),
  );
  setView(
    Math.round((w - bb.width * scale) / 2 - bb.x * scale),
    Math.round((h - bb.height * scale) / 2 - bb.y * scale),
    scale,
  );
  applyView();
}

/** Wire the pan / zoom gestures onto ``host`` once. */
function wireGestures(host: HTMLElement): void {
  const h = host as HTMLElement & { _dagCanvasWired?: boolean };
  if (h._dagCanvasWired) return;
  h._dagCanvasWired = true;

  host.addEventListener(
    "wheel",
    (e: WheelEvent) => {
      e.preventDefault();
      dismissOverlays();
      const rect = host.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) {
        // A trackpad pinch and ⌘+wheel arrive identically, as a wheel
        // event with ctrlKey set. Zoom about the pointer so the thing
        // under the cursor stays under it.
        const k = Math.exp(-e.deltaY * 0.01);
        const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, _viewScale * k));
        const px = e.clientX - rect.left;
        const py = e.clientY - rect.top;
        setView(
          px - (px - _viewTx) * (next / _viewScale),
          py - (py - _viewTy) * (next / _viewScale),
          next,
        );
      } else {
        setView(_viewTx - e.deltaX, _viewTy - e.deltaY, _viewScale);
      }
      applyView();
    },
    { passive: false },
  );

  let drag: { x: number; y: number; id: number } | null = null;
  host.addEventListener("pointerdown", (e: PointerEvent) => {
    // A drag that starts on a node belongs to the node — it is how a
    // click reaches it, and panning from there would swallow every
    // click on the graph.
    const t = e.target as Element | null;
    if (t && t.closest && t.closest(".history-node, .history-branch-tag")) {
      return;
    }
    drag = { x: e.clientX, y: e.clientY, id: e.pointerId };
    dismissOverlays();
    host.classList.add("is-panning");
    try {
      host.setPointerCapture(e.pointerId);
    } catch {
      /* a pointer that vanished mid-gesture is not an error */
    }
  });
  host.addEventListener("pointermove", (e: PointerEvent) => {
    if (!drag || e.pointerId !== drag.id) return;
    setView(
      _viewTx + (e.clientX - drag.x),
      _viewTy + (e.clientY - drag.y),
      _viewScale,
    );
    drag = { x: e.clientX, y: e.clientY, id: e.pointerId };
    applyView();
  });
  const endDrag = (e: PointerEvent): void => {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    host.classList.remove("is-panning");
  };
  host.addEventListener("pointerup", endDrag);
  host.addEventListener("pointercancel", endDrag);
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
  wireGestures(host);
  const fresh = _viewSession !== sessionId;
  setViewSession(sessionId);
  if (fresh) {
    // getBBox needs the element laid out; a frame later it is.
    applyView();
    requestAnimationFrame(fitCanvas);
  } else {
    applyView();
  }
}

/** The canvas is gone (empty session, skeleton): forget the handle so a
 *  stale group never receives a transform. */
export function detachCanvas(): void {
  _handle = null;
}
