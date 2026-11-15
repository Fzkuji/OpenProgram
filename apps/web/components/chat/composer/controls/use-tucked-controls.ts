"use client";

/**
 * Tucking the composer's rows away: a click on the input box's edge (a
 * band RIM_IN px inside it and RIM_OUT px outside it, clear of the text
 * and controls) gives the box a small bounce and slides the environment
 * row above it and the controls row below it behind the box, which stays
 * where it is; the next such click brings them back. The choice is
 * remembered per browser.
 *
 * The band's outer half is its own element, a transparent ring behind
 * the box (`bandRef`, RIM_OUT px larger on every side), so a click there
 * lands on the composer whatever lies under it — the chat, a DAG canvas,
 * the row above reaching close to the box. Where the ring overlaps a
 * control of that row it steps aside (pointer-events: none until the
 * pointer moves off the control). The handlers sit on the composer's
 * input area. A click in the box's inner padding (above or below the
 * text, beside the mic and send) focuses the text instead. Only the band
 * shows the hand cursor (data-tuck-rim on the area, composer.module.css).
 */
import { useCallback, useLayoutEffect, useRef, useState, type PointerEvent, type RefObject } from "react";
import { animate, useReducedMotion } from "framer-motion";

const KEY = "openprogram.composer.tucked";

function stored(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

/** Anything in the box that is its own target: text, controls, attachments. */
const OWN_TARGET = "[data-composer-text], button, a, input, textarea, [contenteditable], [role='button'], [data-attachment-strip] *";

/** How far in from the box's edge, and out past it, a click still tucks. */
const RIM_IN = 6;
export const RIM_OUT = 6;

type Spot = "band" | "inner" | null;

/** Where the pointer is: on the edge band, in the box's own padding, or
 *  elsewhere (over something with its own job, or away from the box). */
function spotOf(event: PointerEvent<HTMLElement>, box: HTMLElement | null, band: HTMLElement | null): Spot {
  if (!box) return null;
  const target = event.target as HTMLElement;
  if (band && target === band) return "band";
  if (target.closest(OWN_TARGET) || !box.contains(target)) return null;
  const r = box.getBoundingClientRect();
  const x = event.clientX - r.left, y = event.clientY - r.top;
  return Math.min(x, y, r.width - x, r.height - y) <= RIM_IN ? "band" : "inner";
}

/** Whether a control lies under the ring at this point (the ring sits
 *  over the row above where that row reaches close to the box). */
function controlUnder(band: HTMLElement, x: number, y: number): boolean {
  const under = document.elementsFromPoint(x, y).find(e => e !== band);
  return !!under?.closest(OWN_TARGET);
}

export function useTuckedControls(boxRef: RefObject<HTMLElement | null>, enabled: boolean) {
  const bandRef = useRef<HTMLDivElement | null>(null);
  const [tucked, setTucked] = useState(stored);
  // The box covers the rows while they are tucked or still sliding out;
  // settled and out, the rows' own popovers go back on top of the box.
  const [covering, setCovering] = useState(tucked);
  const reduced = useReducedMotion();

  // The ring follows the box's own rectangle (the box is inset in its
  // stack), RIM_OUT larger on every side. It is placed in the observer's
  // callback, after layout, and only a side that moved is written: a pane
  // that moves resizes or recentres the box in every frame, but rarely
  // all four sides, and an unchanged value written again still reaches
  // every MutationObserver on the page.
  useLayoutEffect(() => {
    const box = boxRef.current, band = bandRef.current;
    if (!enabled || !box || !band) return;
    const set = (side: "left" | "top" | "width" | "height", px: number) => {
      const value = `${px}px`;
      if (band.style[side] !== value) band.style[side] = value;
    };
    // Every read before any write: a read after a written side made the
    // browser lay the page out again inside the callback, in each frame a
    // moving pane resized the box.
    const place = () => {
      const left = box.offsetLeft, top = box.offsetTop, width = box.offsetWidth, height = box.offsetHeight;
      set("left", left - RIM_OUT);
      set("top", top - RIM_OUT);
      set("width", width + 2 * RIM_OUT);
      set("height", height + 2 * RIM_OUT);
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(box);
    if (box.parentElement) observer.observe(box.parentElement);
    return () => observer.disconnect();
  }, [boxRef, enabled]);

  const onAreaPointerDown = useCallback((event: PointerEvent<HTMLElement>) => {
    if (!enabled || event.button !== 0) return;
    const box = boxRef.current;
    const spot = spotOf(event, box, bandRef.current);
    if (!spot || !box) return;
    if (spot === "inner") {
      // Inner padding: act like part of the text field.
      const field = box.querySelector<HTMLElement>("[data-composer-text] textarea, [data-composer-text] [contenteditable]");
      if (field) { event.preventDefault(); field.focus(); }
      return;
    }
    setCovering(true);
    setTucked(previous => {
      const next = !previous;
      try { localStorage.setItem(KEY, next ? "1" : "0"); } catch { /* the choice lasts this page only */ }
      return next;
    });
    if (!reduced) {
      animate(box, { scale: [1, 0.985, 1.006, 1] }, { duration: 0.42, times: [0, 0.3, 0.68, 1], ease: "easeOut" });
    }
  }, [boxRef, enabled, reduced]);

  const onAreaPointerMove = useCallback((event: PointerEvent<HTMLElement>) => {
    const area = event.currentTarget;
    const band = bandRef.current;
    if (band) {
      // The ring steps aside over a control and comes back off it.
      if (event.target === band) {
        if (controlUnder(band, event.clientX, event.clientY)) band.style.pointerEvents = "none";
      } else if (band.style.pointerEvents && !(event.target as HTMLElement).closest(OWN_TARGET)) {
        band.style.pointerEvents = "";
      }
    }
    const rim = enabled && spotOf(event, boxRef.current, band) === "band";
    if (rim !== area.hasAttribute("data-tuck-rim")) area.toggleAttribute("data-tuck-rim", rim);
  }, [boxRef, enabled]);

  const onAreaPointerLeave = useCallback((event: PointerEvent<HTMLElement>) => {
    event.currentTarget.removeAttribute("data-tuck-rim");
    if (bandRef.current) bandRef.current.style.pointerEvents = "";
  }, []);

  const onSettled = useCallback(() => setCovering(tucked), [tucked]);

  return { tucked: enabled && tucked, covering: enabled && covering, enabled, bandRef, onAreaPointerDown, onAreaPointerMove, onAreaPointerLeave, onSettled };
}
