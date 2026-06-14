"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";
import { renderMathIn } from "./markdown";

// "Stick to bottom" follows the last message's tail through streaming
// updates. User-initiated scroll-up detaches; scrolling back near the
// bottom re-attaches. Mirrors `_setupStickToBottomListener` +
// `scrollToBottom` from web/public/js/shared/helpers.js but as a React
// hook scoped to a container ref instead of the global `#chatArea`.

interface ScrollOpts {
  /** Override the stick-to-bottom check (used after `load_session` to
   * land on the latest turn even if the user was scrolled up in the
   * previous session). */
  force?: boolean;
}

interface UseStickToBottom {
  /** Current stick state. Reads the ref so callers can branch on it
   * without re-rendering when the state flips. */
  stickToBottom: () => boolean;
  /** Anchor the last `.message` bubble's bottom to the viewport bottom
   * (16px breathing margin), running KaTeX render on any new
   * `.md-rendered` nodes first. No-op if not currently stuck unless
   * `force: true`. */
  scrollToBottom: (opts?: ScrollOpts) => void;
}

/** Stick-to-bottom helper bound to a scroll container ref. Pair the
 * returned ref's element with the `.message` child bubbles you want to
 * anchor on. */
export function useStickToBottom(
  containerRef: RefObject<HTMLElement | null>,
): UseStickToBottom {
  const stickRef = useRef(true);

  useEffect(() => {
    const area = containerRef.current;
    if (!area) return;

    const onScroll = () => {
      const distFromBottom =
        area.scrollHeight - area.scrollTop - area.clientHeight;
      // Within 60px of the bottom = stick. Anything further = detach.
      stickRef.current = distFromBottom < 60;
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    return () => area.removeEventListener("scroll", onScroll);
  }, [containerRef]);

  const stickToBottom = useCallback(() => stickRef.current, []);

  const scrollToBottom = useCallback(
    (opts?: ScrollOpts) => {
      const area = containerRef.current;
      if (!area) return;
      renderMathIn(area);
      const force = !!opts?.force;
      if (!force && !stickRef.current) return;
      requestAnimationFrame(() => {
        // Anchor the last message's bottom to the viewport bottom
        // (with a 16px breathing margin) instead of scrolling to the
        // padding edge, so streaming text doesn't get hidden above the
        // 40vh empty pad.
        const bubbles = area.querySelectorAll<HTMLElement>(".message");
        const last = bubbles.length ? bubbles[bubbles.length - 1] : null;
        if (!last) {
          area.scrollTop = area.scrollHeight;
          return;
        }
        const areaRect = area.getBoundingClientRect();
        const msgRect = last.getBoundingClientRect();
        const delta = msgRect.bottom - areaRect.bottom + 16;
        if (delta > 0) area.scrollTop += delta;
        // Clamp: never scroll past natural max.
        const maxTop = area.scrollHeight - area.clientHeight;
        if (area.scrollTop > maxTop) area.scrollTop = maxTop;
      });
    },
    [containerRef],
  );

  return { stickToBottom, scrollToBottom };
}
