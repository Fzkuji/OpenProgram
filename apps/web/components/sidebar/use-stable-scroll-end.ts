import { useEffect, type RefObject } from "react";

/**
 * Keep a scroller's rows still when its content shrinks near the end.
 *
 * Deleting a row from a list scrolled to the bottom makes the browser pull
 * scrollTop back, so every row above the gap slides down into it. A list
 * should close a gap from below instead. When the content shrinks, the
 * scroller keeps its scrollTop and a blank spacer at the end stands in for
 * the lost height. The spacer only ever shrinks to what is still on screen
 * as the reader scrolls up, so it leaves without moving anything.
 */
export function useStableScrollEnd(
  scrollerRef: RefObject<HTMLElement | null>,
  spacerRef: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    const scroller = scrollerRef.current;
    const spacer = spacerRef.current;
    if (!scroller || !spacer || typeof ResizeObserver === "undefined") return;
    let lastTop = scroller.scrollTop;
    let spare = 0;
    const setSpare = (value: number) => {
      spare = Math.max(0, value);
      spacer.style.height = spare ? `${spare}px` : "";
    };
    const contentHeight = () => scroller.scrollHeight - spare;
    let lastContent = contentHeight();

    // Runs on every scroll and content resize. The clamp's own scroll event
    // can arrive before the resize, so either one may be first to see it.
    const settle = () => {
      const content = contentHeight();
      const top = scroller.scrollTop;
      const atEnd = top >= scroller.scrollHeight - scroller.clientHeight - 1;
      if (content < lastContent && top < lastTop && atEnd) {
        // The content shrank and the browser pulled the reader back:
        // hold them where they were.
        setSpare(spare + lastTop - top);
        scroller.scrollTop = lastTop;
      } else {
        lastTop = top;
        // Give back whatever spacer has left the screen. scrollTop snaps
        // to device pixels, so sub-pixel differences are not a move.
        const onScreen = top + scroller.clientHeight - content;
        if (spare && onScreen < spare - 1) setSpare(onScreen);
      }
      lastContent = content;
    };

    const content = new ResizeObserver(settle);
    const observeChildren = () => {
      content.disconnect();
      for (const child of Array.from(scroller.children)) {
        if (child !== spacer) content.observe(child);
      }
    };
    observeChildren();
    const children = new MutationObserver(observeChildren);
    children.observe(scroller, { childList: true });
    // A resized scroller (window, panel) settles the ordinary way.
    let firstViewport = true;
    const viewport = new ResizeObserver(() => {
      if (firstViewport) { firstViewport = false; return; }
      setSpare(0);
      lastTop = scroller.scrollTop;
      lastContent = contentHeight();
    });
    viewport.observe(scroller);
    scroller.addEventListener("scroll", settle, { passive: true });
    return () => {
      content.disconnect();
      children.disconnect();
      viewport.disconnect();
      scroller.removeEventListener("scroll", settle);
      setSpare(0);
    };
  }, [scrollerRef, spacerRef]);
}
