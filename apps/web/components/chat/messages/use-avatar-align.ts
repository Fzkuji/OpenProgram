"use client";

/**
 * Align a message's side avatar to the FIRST LINE OF TEXT inside the
 * message — not to the message/bubble/function-call box's top border.
 *
 * Borders, padding and rounded corners are not part of the height basis:
 * the avatar should always sit level with the first line of actual text,
 * whether that text is plain assistant prose, inside a user bubble (which
 * has its own padding), or inside a function-call card's header (margin +
 * border + header padding). A fixed CSS `top` can't cover all three, so we
 * measure the first text line's position after render and set the avatar
 * top to match.
 *
 * Returns a ref to put on the message container and the avatar `top` (px).
 */
import { useLayoutEffect, useRef, useState } from "react";

export function useAvatarAlign(deps: unknown): {
  containerRef: React.RefObject<HTMLDivElement>;
  avatarTop: number;
} {
  const containerRef = useRef<HTMLDivElement>(null);
  const [avatarTop, setAvatarTop] = useState(16); // sensible default

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    function measure() {
      const root = containerRef.current;
      if (!root) return;
      // First non-empty text node anywhere in the message content (skip the
      // header/avatar itself).
      const header = root.querySelector(".message-header");
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(n) {
          if (!n.textContent || !n.textContent.trim()) return NodeFilter.FILTER_REJECT;
          if (header && header.contains(n)) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      });
      const tn = walker.nextNode();
      if (!tn) return;
      const range = document.createRange();
      range.selectNodeContents(tn);
      const rects = range.getClientRects();
      if (!rects.length) return;
      const lineRect = rects[0];
      const rootTop = root.getBoundingClientRect().top;
      // CENTER-align: the avatar's vertical center sits on the first text
      // line's vertical center (the avatar is taller than one line, so its
      // top moves up by half the height difference). Not top-aligned.
      const avatar = root.querySelector(".message-avatar") as HTMLElement | null;
      const avatarH = avatar ? avatar.getBoundingClientRect().height : 28;
      const lineCenter = lineRect.top + lineRect.height / 2;
      const top = Math.round(lineCenter - avatarH / 2 - rootTop);
      if (top !== null && Number.isFinite(top)) setAvatarTop(Math.max(0, top));
    }

    measure();
    // Re-measure on size changes (streaming text growing, blocks expanding).
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deps]);

  return { containerRef, avatarTop };
}
