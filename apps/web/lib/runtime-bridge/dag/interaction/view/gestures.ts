/**
 * The canvas's pointer gestures (dag/rendering.md §4), wired once per
 * pane by ``./canvas.ts``, which owns the view they move:
 *
 *   * mouse wheel or trackpad pinch → zoom about the cursor
 *   * two fingers on a trackpad → pan (``./wheel.ts`` tells them apart)
 *   * right (or middle) drag → pan, from anywhere on the graph
 *   * left drag on empty space → a box that selects (``../selection/selection.ts``;
 *     a left press on a node is a click on it)
 */

import { hasPrimaryModifier } from "../../../../shortcut-keys";
import { wheelKind } from "./wheel";
import { SelectionBand, clearSelection, releaseEditableFocus, select } from "../selection/selection";
import { _viewScale, _viewTx, _viewTy, setView } from "../../store/globals";

/** Zoom rates. A pinch (ctrlKey wheel) delivers small continuous
 *  deltas while a plain wheel delivers ±100-ish notches — one shared
 *  rate would make one of the two violent. */
const PINCH_ZOOM_RATE = 0.01;
export const WHEEL_ZOOM_RATE = 0.002;

/** What the gestures need from the view's owner. */
export interface Camera {
  /** Zoom by ``factor`` about the screen point ``(px, py)``. */
  zoomAt(px: number, py: number, factor: number): void;
  /** Close the screen-anchored cards before the view moves under them. */
  dismiss(): void;
  /** The store's view moved; paint it. */
  moved(): void;
  /** Keep reporting the cursor while ``held`` stays down, even outside the
   *  window. Returns the stop, or null where that cannot be done. */
  followHeldButton?(
    held: number,
    onCursor: (point: ScreenPoint) => void,
    onReleased: () => void,
  ): (() => void) | null;
}

interface ScreenPoint { x: number; y: number }

/** Wire the pan / zoom gestures onto ``host`` once. */
export function wireGestures(host: HTMLElement, camera: Camera): void {
  const h = host as HTMLElement & { _dagCanvasWired?: boolean };
  if (h._dagCanvasWired) return;
  h._dagCanvasWired = true;

  host.addEventListener(
    "wheel",
    (e: WheelEvent) => {
      e.preventDefault();
      camera.dismiss();
      const rect = host.getBoundingClientRect();
      // A mouse wheel zooms about the cursor, the way a map does; two
      // fingers on a trackpad pan, and a pinch zooms at its own, faster
      // rate (./wheel.ts tells them apart). Line and page deltas (some
      // mice) are turned into pixels first.
      const kind = wheelKind(e);
      if (kind === "pan") {
        setView(_viewTx - e.deltaX, _viewTy - e.deltaY, _viewScale);
        camera.moved();
        return;
      }
      const dy = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaMode === 2 ? e.deltaY * 100 : e.deltaY;
      const rate = kind === "pinch" ? PINCH_ZOOM_RATE : WHEEL_ZOOM_RATE;
      camera.zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-dy * rate));
    },
    { passive: false },
  );

  // The right (or middle) button pans, from anywhere on the graph; the
  // left button selects — a drag on empty canvas draws a box (Shift/⌘
  // adds to the selection), and a press that never moves clears it.
  // "Never moves" means less than CLICK_SLOP pixels, whatever the hand's
  // jitter. A right press that never moves is still the right-click
  // menu: macOS fires ``contextmenu`` on the press, before anyone knows
  // whether a drag follows, so the canvas holds the event and replays
  // it on release (``menuAt``).
  //
  // A release outside the window can go missing: no ``pointerup`` ever
  // arrives, and the next plain move would still look like the drag. So
  // every move also asks the mouse itself (``PointerEvent.buttons``)
  // whether the button that began the gesture is still down.
  //
  // The same loss stops the moves past the window's edge, so once a pan
  // is really moving the desktop App reports the cursor as well
  // (``followHeldButton``). Those reports only count while the page's
  // own moves have gone quiet, or the two would jitter against each other.
  const CLICK_SLOP = 3;
  const PAGE_EVENTS_QUIET_MS = 40;
  const LEFT_BUTTON = 1;
  const RIGHT_BUTTON = 2;
  const MIDDLE_BUTTON = 4;
  interface PanDrag {
    x: number; y: number; sx: number; sy: number; id: number; moved: boolean;
    menuAt: Element | null; held: number;
    eventAt: number; cursor: ScreenPoint | null; unfollow: (() => void) | null;
  }
  let drag: PanDrag | null = null;
  let band: { b: SelectionBand; id: number; sx: number; sy: number; moved: boolean; add: boolean } | null = null;
  let replaying = false;
  host.addEventListener("contextmenu", (e) => {
    if (replaying) return;
    e.preventDefault();
    e.stopPropagation();
  }, true);
  host.addEventListener("pointerdown", (e: PointerEvent) => {
    const t = e.target as Element | null;
    // A drag from an overlay card (expanded tooltip, node menu, raw
    // JSON) is the user SELECTING TEXT, not panning — and the pan's
    // dismissOverlays() would eat the card mid-selection.
    if (t?.closest?.(".history-tooltip, .dag-inspector, .dag-menu-verbs")) return;
    const pans = e.button === 2 || e.button === 1;
    // A left press on a node or a branch name is a click on it.
    if (!pans && t?.closest?.(".history-node, .history-branch-tag")) return;
    camera.dismiss();
    try {
      host.setPointerCapture(e.pointerId);
    } catch {
      /* a pointer that vanished mid-gesture is not an error */
    }
    if (!pans) {
      releaseEditableFocus();
      band = {
        b: new SelectionBand(host, { x: e.clientX, y: e.clientY }),
        id: e.pointerId, sx: e.clientX, sy: e.clientY, moved: false,
        add: e.shiftKey || hasPrimaryModifier(e),
      };
      return;
    }
    drag = {
      x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY,
      id: e.pointerId, moved: false, menuAt: e.button === 2 ? t : null,
      held: e.button === 2 ? RIGHT_BUTTON : MIDDLE_BUTTON,
      eventAt: performance.now(), cursor: null, unfollow: null,
    };
    host.classList.add("is-panning");
  });
  const panTo = (d: PanDrag, x: number, y: number): void => {
    setView(_viewTx + (x - d.x), _viewTy + (y - d.y), _viewScale);
    d.x = x;
    d.y = y;
    camera.moved();
    if (d.moved || Math.hypot(x - d.sx, y - d.sy) < CLICK_SLOP) return;
    d.moved = true;
    d.unfollow = camera.followHeldButton?.(
      d.held,
      (point) => {
        const last = d.cursor;
        d.cursor = point;
        if (drag !== d || !last || performance.now() - d.eventAt < PAGE_EVENTS_QUIET_MS) return;
        panTo(d, d.x + point.x - last.x, d.y + point.y - last.y);
      },
      () => { if (drag === d) stopPan(d); },
    ) ?? null;
  };
  host.addEventListener("pointermove", (e: PointerEvent) => {
    if (band && e.pointerId === band.id) {
      if (!(e.buttons & LEFT_BUTTON)) {
        endDrag(e);
        return;
      }
      if (!band.moved
          && Math.hypot(e.clientX - band.sx, e.clientY - band.sy) < CLICK_SLOP) {
        return;
      }
      band.moved = true;
      band.b.move({ x: e.clientX, y: e.clientY });
      return;
    }
    if (!drag || e.pointerId !== drag.id) return;
    if (!(e.buttons & drag.held)) {
      endDrag(e);
      return;
    }
    drag.eventAt = performance.now();
    panTo(drag, e.clientX, e.clientY);
  });
  // A release the browser lost leaves the pointer captured, which would
  // keep every later hover on the canvas.
  const letGo = (pointerId: number): void => {
    if (host.hasPointerCapture(pointerId)) host.releasePointerCapture(pointerId);
  };
  const stopPan = (d: PanDrag): void => {
    drag = null;
    d.unfollow?.();
    letGo(d.id);
    host.classList.remove("is-panning");
  };
  // ``e`` is a ``pointermove`` when the release itself went missing: the
  // box still counts as let go, but a menu would open at the wrong moment.
  const endDrag = (e: PointerEvent): void => {
    if (band && e.pointerId === band.id) {
      const { b, moved, add } = band;
      band = null;
      letGo(e.pointerId);
      const released = e.type !== "pointercancel";
      const hits = moved && released ? b.hits() : [];
      b.remove();
      if (hits.length) select(hits, add ? "add" : "replace");
      else if (!add && released) clearSelection();
      return;
    }
    if (!drag || e.pointerId !== drag.id) return;
    const { menuAt, moved } = drag;
    stopPan(drag);
    if (menuAt && !moved && e.type === "pointerup") {
      replaying = true;
      menuAt.dispatchEvent(new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true, clientX: e.clientX, clientY: e.clientY, button: 2,
      }));
      replaying = false;
    }
  };
  host.addEventListener("pointerup", endDrag);
  host.addEventListener("pointercancel", endDrag);
}
