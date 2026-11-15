"use client";

/**
 * The chat mask follows the tucked rows. The mask over the transcript
 * (composer.module.css, .inputArea::before/::after) starts at the top of
 * the composer, where the environment row sits. A tucked row keeps its
 * height so the box never moves (tuck-row.tsx), which left an empty strip
 * of mask above the box covering the transcript. While the row above is
 * tucked the mask starts SHIFT px lower, MARGIN above whatever comes next
 * (queued messages or the box); untucked it is back at the top. The shift
 * moves on the rows' own spring, in one pass, and is written as
 * COMPOSER_TUCK_SHIFT_VAR on the composer root; each change fires
 * COMPOSER_TUCK_SHIFT_EVENT so the transcript's reserve and the Jump to
 * latest chip follow the covered height (visibleComposerHeight).
 */
import { useLayoutEffect, useRef, type RefObject } from "react";
import { animate, useReducedMotion } from "framer-motion";
import { COMPOSER_TUCK_SHIFT_EVENT, COMPOSER_TUCK_SHIFT_VAR } from "@/lib/chat/history/chat-scroll";
import { TUCK_SLIDE } from "./tuck-row";

/** Solid mask kept above the box: the composer's own 12px padding. */
export const BACKDROP_MARGIN = 12;

/** How far down the mask can start with the row above tucked: down to
 *  MARGIN above the next row. Nothing when panels sit above that row. */
export function tuckedShift(area: HTMLElement): number {
  const row = area.querySelector<HTMLElement>(":scope > [data-tuck-side='above']");
  const next = row?.nextElementSibling as HTMLElement | null | undefined;
  if (!row || !next) return 0;
  const before = row.previousElementSibling as HTMLElement | null;
  if (before && before.offsetHeight > 0) return 0;
  return Math.max(0, next.offsetTop - row.offsetTop - BACKDROP_MARGIN);
}

export function useTuckBackdrop(areaRef: RefObject<HTMLElement | null>, tucked: boolean) {
  const reduced = useReducedMotion();
  const shift = useRef(0);
  const first = useRef(true);

  useLayoutEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    const write = (value: number) => {
      shift.current = value;
      area.style.setProperty(COMPOSER_TUCK_SHIFT_VAR, `${value}px`);
      area.dispatchEvent(new Event(COMPOSER_TUCK_SHIFT_EVENT));
    };
    const target = tucked ? tuckedShift(area) : 0;
    // A page that opens with the rows tucked starts settled, as the rows do.
    const instant = first.current || reduced;
    first.current = false;
    let controls: ReturnType<typeof animate> | null = null;
    let moving = false;
    if (instant || target === shift.current) write(target);
    else {
      moving = true;
      controls = animate(shift.current, target, {
        ...TUCK_SLIDE,
        onUpdate: write,
        onComplete: () => { moving = false; },
      });
    }

    if (!tucked || typeof ResizeObserver === "undefined") return () => controls?.stop();
    // Tucked and settled: a row above or below the gap that changes height
    // (queued messages arriving, the row rewrapping) moves the mask with it.
    const observer = new ResizeObserver(() => {
      if (moving) return;
      const next = tuckedShift(area);
      if (next !== shift.current) write(next);
    });
    for (const child of Array.from(area.children)) observer.observe(child);
    return () => { controls?.stop(); observer.disconnect(); };
  }, [areaRef, tucked, reduced]);
}
