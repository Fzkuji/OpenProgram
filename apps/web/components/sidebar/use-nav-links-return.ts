"use client";

import { useLayoutEffect, useRef } from "react";
import type { RefObject } from "react";
import { RAIL_EASING, RAIL_MOVE_MS } from "../layout/use-resizable-rail";

/** Brings the sidebar's links back to the top of the scroll area when it
 *  closes with its list scrolled.
 *
 *  The links scroll with the list, and the collapsed sidebar shows their
 *  icons where they sit with the list at its start. Closing a scrolled
 *  sidebar moves them there with the rail: from the click's frame they
 *  slide down by the scrolled distance, on the rail's duration and curve,
 *  over the fading list (starting just above the visible top when they are
 *  scrolled out of sight entirely, so the slide shows only the last part
 *  of the way). When the close settles, the scroll position goes back to
 *  the start and the slide is dropped in the same frame, which leaves the
 *  links where they are. Reopening before then plays the slide back.
 *  One Web Animation per close, on transform: nothing runs per frame on
 *  the main thread. */
export function useNavLinksReturn(
  open: boolean,
  closedSettled: boolean,
  scrollerRef: RefObject<HTMLElement | null>,
  linksRef: RefObject<HTMLElement | null>,
) {
  const slide = useRef<Animation | null>(null);
  useLayoutEffect(() => {
    const running = slide.current;
    // Reopening mid-close plays the slide back; closing again mid-way
    // plays it forward again.
    if (running) {
      running.reverse();
      return;
    }
    if (open) return;
    const scroller = scrollerRef.current;
    const links = linksRef.current;
    if (!scroller || !links || typeof links.animate !== "function") return;
    const scrolled = scroller.scrollTop;
    if (scrolled <= 0) return;
    const hiddenBy = scroller.getBoundingClientRect().top - links.getBoundingClientRect().bottom;
    const from = Math.max(0, Math.min(scrolled, hiddenBy));
    const animation = links.animate(
      [{ transform: `translateY(${from}px)` }, { transform: `translateY(${scrolled}px)` }],
      { duration: RAIL_MOVE_MS, easing: RAIL_EASING, fill: "both" },
    );
    animation.onfinish = () => {
      // Played back to the open place: nothing is left to hold.
      if (animation.playbackRate >= 0) return;
      animation.cancel();
      if (slide.current === animation) slide.current = null;
    };
    slide.current = animation;
  }, [open, scrollerRef, linksRef]);
  useLayoutEffect(() => {
    if (!closedSettled) return;
    slide.current?.cancel();
    slide.current = null;
    const scroller = scrollerRef.current;
    if (scroller && scroller.scrollTop !== 0) scroller.scrollTop = 0;
  }, [closedSettled, scrollerRef]);
}
