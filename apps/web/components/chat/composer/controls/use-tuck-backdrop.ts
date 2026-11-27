"use client";

/**
 * The chat mask follows the tucked rows. The mask over the transcript
 * (composer.module.css, .inputArea::before/::after) starts at the top of
 * the composer, where the environment row sits. A tucked row keeps its
 * height so the box never moves (tuck-row.tsx), which left an empty strip
 * of mask above the box covering the transcript. While the row above is
 * tucked the mask starts SHIFT px lower, MARGIN above whatever comes next
 * (queued messages or the box); untucked it is back at the top.
 *
 * The shift is written once, as its end value, in COMPOSER_TUCK_SHIFT_VAR
 * on the composer root; it moves the mask by a transform, and while a
 * toggle slides the root carries COMPOSER_SLIDING_ATTR, which gives that
 * transform the rows' spring as a CSS transition. The transcript does not
 * restyle during the slide: COMPOSER_RESERVE_SLIDE_EVENT tells it, at the
 * toggle, how far its reserve will move, so a transcript following the
 * latest rides that distance as a transform (use-chat-area-stick.ts), and
 * COMPOSER_TUCK_SHIFT_EVENT fires once the slide has ended, when the
 * height publishers write the new covered height (visibleComposerHeight)
 * and the ride is dropped in the same frame. Every frame of the slide is
 * the compositor's; the page's own style and layout work happens at the
 * toggle (the box and its rows only) and after the slide.
 */
import { useLayoutEffect, type RefObject } from "react";
import { useReducedMotion } from "framer-motion";
import {
  COMPOSER_RESERVE_SLIDE_EVENT,
  COMPOSER_TUCK_SHIFT_EVENT,
  COMPOSER_TUCK_SHIFT_VAR,
  COMPOSER_SLIDING_ATTR,
  type ReserveSlide,
} from "@/lib/chat/history/chat-scroll";
import type { TuckStore } from "./use-tucked-controls";

/** Solid mask kept above the box: the composer's own 12px padding. */
export const BACKDROP_MARGIN = 12;

/** How long a toggle's slide lasts: --composer-tuck-duration, with a
 *  margin for the last frame. */
const SLIDE_MS = 560;

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

/** The slide's timing, from the --composer-tuck-* tokens. */
function slideTiming(area: HTMLElement): { duration: number; easing: string } {
  const style = getComputedStyle(area);
  const duration = parseFloat(style.getPropertyValue("--composer-tuck-duration"));
  return {
    duration: Number.isFinite(duration) ? duration : 500,
    easing: style.getPropertyValue("--composer-tuck-easing").trim() || "ease-out",
  };
}

export function useTuckBackdrop(areaRef: RefObject<HTMLElement | null>, store: TuckStore, enabled: boolean) {
  const reduced = useReducedMotion();

  useLayoutEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    let shift = 0;
    let tucked: boolean | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // The tokens are fixed: read once here, not in the click, where the
    // read forced a style pass right after the mask's shift was written.
    const timing = slideTiming(area);
    const publish = () => area.dispatchEvent(new Event(COMPOSER_TUCK_SHIFT_EVENT));
    const write = (value: number) => {
      shift = value;
      area.style.setProperty(COMPOSER_TUCK_SHIFT_VAR, `${value}px`);
    };
    const settle = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      area.removeAttribute(COMPOSER_SLIDING_ATTR);
    };
    const apply = () => {
      const next = enabled && store.get().tucked;
      if (next === tucked) return;
      // A page that opens with the rows tucked starts settled, as the rows do.
      const instant = tucked === null || reduced;
      tucked = next;
      const from = shift;
      const target = next ? tuckedShift(area) : 0;
      settle();
      write(target);
      if (instant || target === from) { publish(); return; }
      area.setAttribute(COMPOSER_SLIDING_ATTR, "");
      const detail: ReserveSlide = { by: target - from, ...timing };
      area.dispatchEvent(new CustomEvent(COMPOSER_RESERVE_SLIDE_EVENT, { bubbles: true, detail }));
      timer = setTimeout(() => { settle(); publish(); }, SLIDE_MS);
    };
    apply();
    const off = store.subscribe(apply);

    // Tucked and settled: a row above or below the gap that changes height
    // (queued messages arriving, the row rewrapping) moves the mask with it.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      if (!tucked || timer) return;
      const next = tuckedShift(area);
      if (next !== shift) { write(next); publish(); }
    });
    if (observer) for (const child of Array.from(area.children)) observer.observe(child);
    return () => {
      off();
      observer?.disconnect();
      if (timer) { settle(); publish(); }
    };
  }, [areaRef, store, enabled, reduced]);
}
