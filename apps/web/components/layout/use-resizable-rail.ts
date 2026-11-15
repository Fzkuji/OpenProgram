"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

// Keep in sync with --rail-collapsed-w in app/styles/design/layout.css.
const COLLAPSED_RAIL_WIDTH = 62;
// The rail moves the whole centre column, so the curve starts from rest:
// one that front-loads the travel puts a third of it into the first frame.
export const RAIL_MOVE_MS = 200;
export const RAIL_EASING = "cubic-bezier(0.4, 0, 0.2, 1)";
const RAIL_TRANSITION = `width ${RAIL_MOVE_MS}ms ${RAIL_EASING}, min-width ${RAIL_MOVE_MS}ms ${RAIL_EASING}`;
// A move whose width does not report its end settles after this.
const SETTLE_FALLBACK_MS = RAIL_MOVE_MS + 120;
const KEYBOARD_STEP = 24;
// A drag this close to a snap width rests on it.
const SNAP_REACH = 8;

/** Where a drag at `width` rests: on the nearest snap width within `reach`,
 *  and where it is everywhere else. */
export function snapWidth(width: number, snaps: readonly number[], reach = SNAP_REACH): number {
  let best = width;
  let distance = reach + 1;
  for (const snap of snaps) {
    const off = Math.abs(width - snap);
    if (off <= reach && off < distance) { best = snap; distance = off; }
  }
  return best;
}
// The way back to the default width. It is walked a frame at a time like a
// drag: a transition would move the rail's edge alone, while everything
// inside, laid out at --rail-open-w, jumped to the new width at once.
const RESET_MS = 280;
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);
// At or below this the rail floats over the page (styles/base/layout.css),
// so its width takes nothing from the centre column.
const FLOATING_WINDOW_WIDTH = 900;

/** Makes the rail's open-only parts (`.rail-open-only`, outermost ones)
 *  inert or not, on the elements themselves. */
function setOpenOnlyInert(shell: HTMLElement | null, inert: boolean) {
  if (!shell) return;
  for (const part of shell.querySelectorAll<HTMLElement>(".rail-open-only")) {
    // An inner part is inert with its outer one.
    if (part.parentElement?.closest(".rail-open-only")) continue;
    if (part.hasAttribute("inert") === inert) continue;
    if (inert) part.setAttribute("inert", "");
    else part.removeAttribute("inert");
  }
}

interface ResizableRailOptions {
  open: boolean;
  minWidth: number;
  maxWidth: number;
  defaultWidth: number;
  direction: 1 | -1;
  /** Keeps the width in this browser, lets the arrow keys resize the handle,
   *  and gives the handle its way back to the default: a double-click or
   *  Enter, and a drag snapping to it. */
  storageKey?: string;
  /** The most of the window the rail may show. It limits what is drawn, not
   *  what is stored, so widening the window brings a stored width back. */
  maxWindowShare?: number;
  /** Widths a drag rests on as it passes them, with a storage key. The
   *  default width alone when not given. */
  snapWidths?: readonly number[];
}

function readStoredWidth(storageKey: string): number | null {
  try {
    const stored = Number(window.localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? stored : null;
  } catch {
    return null;
  }
}

/** A rail at its default width stores nothing, so it follows the default if
 *  that ever changes. */
function storeWidth(storageKey: string, width: number, defaultWidth: number) {
  try {
    if (width === defaultWidth) window.localStorage.removeItem(storageKey);
    else window.localStorage.setItem(storageKey, String(width));
  } catch {
    // The width still holds for this page.
  }
}

/** The rail's width, its resize handle, and when a close has settled.
 *
 *  Opening and closing change only the shell's width, in the click's
 *  frame, and the rail's `.collapsed` class, which the rails set from
 *  `open` in the same render. Nothing inside the rail mounts, unmounts or
 *  changes its display on either move (styles/base/layout.css, "Opening
 *  and closing"): what only the open rail shows stays mounted at the open
 *  width and fades, what both states show stays put or moves with the
 *  card's edge on the width's own duration and curve, which the shell
 *  passes down as --rail-move-duration and --rail-move-easing. */
export function useResizableRail({
  open,
  minWidth,
  maxWidth,
  defaultWidth,
  direction,
  storageKey,
  maxWindowShare,
  snapWidths,
}: ResizableRailOptions) {
  const snaps = snapWidths ?? [defaultWidth];
  const snapsRef = useRef(snaps);
  snapsRef.current = snaps;
  const [width, setWidth] = useState(defaultWidth);
  const widthRef = useRef(width);
  widthRef.current = width;
  const [windowWidth, setWindowWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    if (!maxWindowShare) return;
    const read = () => setWindowWidth(window.innerWidth);
    read();
    window.addEventListener("resize", read);
    return () => window.removeEventListener("resize", read);
  }, [maxWindowShare]);
  const limit = maxWindowShare && windowWidth !== null && windowWidth > FLOATING_WINDOW_WIDTH
    ? Math.max(minWidth, Math.min(maxWidth, Math.round(windowWidth * maxWindowShare)))
    : maxWidth;
  const shownWidth = Math.min(width, limit);
  const limitRef = useRef(limit);
  limitRef.current = limit;
  useLayoutEffect(() => {
    if (!storageKey) return;
    const stored = readStoredWidth(storageKey);
    if (stored !== null) setWidth(Math.max(minWidth, Math.min(maxWidth, stored)));
  }, [storageKey, minWidth, maxWidth]);
  const [resizing, setResizing] = useState(false);
  // Closed, and the close has arrived: false from the render that opens,
  // true once a close's width has settled (its transitionend, or the timer
  // behind it). Nothing visible keys on it; it is for what may change only
  // once the rail is at rest collapsed (the narrow layout's flow and drawer
  // shadow, the left sidebar's scroll position, paused animations).
  const [closedSettled, setClosedSettled] = useState(!open);
  if (open && closedSettled) setClosedSettled(false);
  // A close before the rail's first frame has been drawn is no move: it is
  // the mount restoring a stored closed rail (the left sidebar, under
  // html[data-sidebar-closed]), and nothing has been seen open.
  const drawn = useRef(false);
  useEffect(() => {
    const id = window.requestAnimationFrame(() => { drawn.current = true; });
    return () => window.cancelAnimationFrame(id);
  }, []);
  if (!open && !closedSettled && !drawn.current) setClosedSettled(true);
  // What only the open rail shows is unreachable (inert: no pointer, no
  // focus, out of the accessibility tree) from a close's settle to an
  // open's settle; while either move runs it is reachable or not as it was
  // at rest. The hook writes the attribute on the parts directly at the
  // settle, after the move: inert changes the whole subtree's style, and
  // in the click's frame it would restyle the whole conversation list
  // (as `pointer-events` and `visibility` did: 1,764 and 1,946 elements,
  // traced). Hidden by opacity alone, the parts change no inherited
  // property on a move (styles/base/layout.css).
  const shellRef = useRef<HTMLDivElement | null>(null);
  const inert = useRef(false);
  const settleInert = useCallback((next: boolean) => {
    inert.current = next;
    setOpenOnlyInert(shellRef.current, next);
  }, []);
  const settleClose = useCallback(() => {
    settleInert(true);
    setClosedSettled(true);
  }, [settleInert]);
  useEffect(() => {
    if (open || closedSettled) return;
    // transitionend settles it; the timer covers a rail whose width did
    // not change or whose transition was cut short.
    const timer = window.setTimeout(settleClose, SETTLE_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [open, closedSettled, settleClose]);
  useEffect(() => {
    if (!open || !inert.current) return;
    const timer = window.setTimeout(() => settleInert(false), SETTLE_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [open, settleInert]);
  // A closed mount (a stored closed rail) starts inert, and a part that
  // mounts while the rail is at rest closed (a first favourite) becomes
  // inert with it.
  useLayoutEffect(() => {
    if (closedSettled && !open) settleInert(true);
    else if (inert.current) setOpenOnlyInert(shellRef.current, true);
  });
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [gliding, setGliding] = useState(false);
  const glide = useRef(0);
  const stopGlide = useCallback(() => {
    if (!glide.current) return;
    window.cancelAnimationFrame(glide.current);
    glide.current = 0;
    setGliding(false);
  }, []);
  useEffect(() => stopGlide, [stopGlide]);


  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      // The handle stays mounted in both states; only the open rail resizes.
      if (event.button !== 0 || !open) return;
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      stopGlide();
      dragRef.current = { startX: event.clientX, startWidth: shownWidth };
      setResizing(true);
    },
    [shownWidth, open, stopGlide],
  );

  const finishResize = useCallback(() => {
    if (dragRef.current && storageKey) storeWidth(storageKey, widthRef.current, defaultWidth);
    dragRef.current = null;
    setResizing(false);
  }, [storageKey, defaultWidth]);

  const resetWidth = useCallback(() => {
    if (!storageKey || !open) return;
    storeWidth(storageKey, defaultWidth, defaultWidth);
    stopGlide();
    const from = Math.min(widthRef.current, limitRef.current);
    const still = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still || Math.abs(from - defaultWidth) < 2) { setWidth(defaultWidth); return; }
    const started = performance.now();
    setGliding(true);
    const step = () => {
      const progress = Math.min(1, (performance.now() - started) / RESET_MS);
      setWidth(Math.round(from + (defaultWidth - from) * easeOut(progress)));
      if (progress < 1) { glide.current = window.requestAnimationFrame(step); return; }
      glide.current = 0;
      setGliding(false);
    };
    glide.current = window.requestAnimationFrame(step);
  }, [defaultWidth, open, stopGlide, storageKey]);

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Enter") {
        event.preventDefault();
        resetWidth();
        return;
      }
      if (!storageKey || !open || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
      event.preventDefault();
      stopGlide();
      const moved = (event.key === "ArrowRight" ? KEYBOARD_STEP : -KEYBOARD_STEP) * direction;
      const next = Math.max(minWidth, Math.min(limitRef.current, Math.min(widthRef.current, limitRef.current) + moved));
      setWidth(next);
      storeWidth(storageKey, next, defaultWidth);
    },
    [defaultWidth, direction, minWidth, open, resetWidth, stopGlide, storageKey],
  );

  // A close ends a drag: the width is no longer the handle's.
  useEffect(() => {
    if (!open && resizing) finishResize();
  }, [open, resizing, finishResize]);

  useEffect(() => {
    if (!resizing) return;
    window.addEventListener("pointerup", finishResize);
    window.addEventListener("pointercancel", finishResize);
    window.addEventListener("blur", finishResize);
    return () => {
      window.removeEventListener("pointerup", finishResize);
      window.removeEventListener("pointercancel", finishResize);
      window.removeEventListener("blur", finishResize);
    };
  }, [resizing, finishResize]);

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag) return;
      if (event.buttons === 0) {
        finishResize();
        return;
      }
      const moved = drag.startWidth + (event.clientX - drag.startX) * direction;
      const next = storageKey ? snapWidth(moved, snapsRef.current) : moved;
      setWidth(Math.max(minWidth, Math.min(limitRef.current, next)));
    },
    [direction, minWidth, finishResize, storageKey],
  );

  const style = {
    width: `${open ? shownWidth : COLLAPSED_RAIL_WIDTH}px`,
    minWidth: `${open ? minWidth : COLLAPSED_RAIL_WIDTH}px`,
    transition: resizing || gliding ? "none" : RAIL_TRANSITION,
    // The open width: what only the open rail shows is laid out at it in
    // both states (styles/base/layout.css). It changes only while the
    // handle is dragged, never on opening or closing.
    "--rail-open-w": `${shownWidth}px`,
    // The rail's move as a duration and a curve, for what moves or fades
    // with the width inside it.
    "--rail-move-duration": `${RAIL_MOVE_MS}ms`,
    "--rail-move-easing": RAIL_EASING,
  } as CSSProperties;

  return {
    style,
    /** Closed and at rest: true once a close has arrived, false from the
     *  render that opens. */
    closedSettled,
    /** Spread on the rail's shell next to `style`. */
    shellProps: {
      ref: shellRef,
      "data-rail-closed": closedSettled ? "" : undefined,
      onTransitionEnd: (event: { target: EventTarget; currentTarget: EventTarget; propertyName: string }) => {
        if (event.target !== event.currentTarget || event.propertyName !== "width") return;
        if (!open) settleClose();
        else if (inert.current) settleInert(false);
      },
    },
    /** The handle is mounted in both states, at one place among the card's
     *  children: mounting it on open and removing it on close changed
     *  whether the rail's content was the card's first child, which
     *  restyled every element that a `:first-child` rule anywhere could
     *  match (2,000 in the left sidebar, about 20 ms of the click's frame,
     *  traced). Closed, or closing, it takes no pointer and is hidden from
     *  assistive technology (styles/base/layout.css). */
    resizeHandleProps: {
      className: "rail-resize-handle",
      "data-edge": direction === 1 ? "right" : "left",
      "data-off": open ? undefined : "",
      "aria-hidden": open ? undefined : true,
      "data-resizing": resizing,
      "data-snapped": resizing && storageKey && snaps.includes(shownWidth) ? "" : undefined,
      onPointerDown,
      onPointerMove,
      onPointerUp: finishResize,
      onPointerCancel: finishResize,
      onLostPointerCapture: finishResize,
      ...(storageKey && open ? {
        role: "separator",
        "aria-orientation": "vertical" as const,
        "aria-label": "Resize panel",
        "aria-valuemin": minWidth,
        "aria-valuemax": limit,
        "aria-valuenow": shownWidth,
        tabIndex: 0,
        onKeyDown,
        onDoubleClick: resetWidth,
      } : {}),
    },
  };
}
