"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { noteChatWidth } from "@/lib/chat/history/message-window";
import { canvasPanesMoving, onCanvasMotionChange } from "@/lib/tabs/canvas/canvas-motion";

const WIDTH_SETTLE_MS = 120;
/** How far the view may drift mid-move before the rows follow it. */
const MOVE_SLACK = 400;
/** Set on the chat area while its width is changing. Rows off screen then
 *  keep their last laid-out size and skip layout (transcript.css), so a
 *  drag re-wraps only what is visible; everything re-wraps once at rest. */
export const WIDTH_SETTLING_ATTR = "data-width-settling";
const ROW_SELECTOR = ":scope > .msg-slot, :scope > .message, :scope > div[style*=contents] > .message";

/** Measured viewport with extra mounted content for large scroll deltas. */
export function useMessageViewport(chatKey: string | null, rowCount: number, paintRows: boolean, areaRef?: RefObject<HTMLElement>) {
  const [view, setView] = useState({ top: 0, h: 800, overscan: 1600 });
  const measureGate = useRef(false);
  const [, setMeasureGen] = useState(0);
  const notifyMeasured = useCallback(() => {
    if (measureGate.current) return;
    measureGate.current = true;
    requestAnimationFrame(() => {
      measureGate.current = false;
      setMeasureGen((n) => n + 1);
    });
  }, []);
  useLayoutEffect(() => {
    if (!paintRows) return;
    const area = areaRef?.current ?? document.getElementById("chatArea");
    if (!area) return;
    const next = { top: area.scrollTop, h: area.clientHeight, overscan: 1600 };
    setView((prev) => (prev.top === next.top && prev.h === next.h ? prev : next));
  }, [chatKey, rowCount, paintRows, areaRef]);
  useEffect(() => {
    if (!paintRows) return;
    const area = areaRef?.current ?? document.getElementById("chatArea");
    if (!area) return;
    let raf = 0;
    let repaintRaf = 0;
    let lastWidth = area.clientWidth;
    let idle: ReturnType<typeof setTimeout> | undefined;
    let widthSettle: ReturnType<typeof setTimeout> | undefined;
    // Chromium can keep a stale raster for one very tall text block (a
    // multi-thousand-line reply) after a width change, leaving the view
    // blank until the next scroll. Re-layering the list for one frame
    // forces a fresh raster without touching scroll position.
    const repaint = () => {
      const list = area.querySelector<HTMLElement>("#chatMessages");
      if (!list) return;
      list.style.willChange = "transform";
      cancelAnimationFrame(repaintRaf);
      repaintRaf = requestAnimationFrame(() => {
        repaintRaf = requestAnimationFrame(() => { list.style.willChange = ""; });
      });
    };
    const beginSettling = () => {
      if (!area.hasAttribute(WIDTH_SETTLING_ATTR)) area.setAttribute(WIDTH_SETTLING_ATTR, "");
    };
    // Rows above the viewport re-wrap when the attribute goes. The chat
    // area has no native scroll anchoring, so hold the first visible row
    // where it is, unless the view sits at the bottom (the stick hook
    // keeps it there).
    const endSettling = () => {
      if (!area.hasAttribute(WIDTH_SETTLING_ATTR)) return;
      const list = area.querySelector<HTMLElement>(".chat-messages");
      const atBottom = area.scrollHeight - area.scrollTop - area.clientHeight < 2;
      let anchor: HTMLElement | null = null;
      let before = 0;
      if (list && !atBottom) {
        const top = area.getBoundingClientRect().top;
        for (const row of list.querySelectorAll<HTMLElement>(ROW_SELECTOR)) {
          const box = row.getBoundingClientRect();
          if (box.bottom > top) { anchor = row; before = box.top; break; }
        }
      }
      area.removeAttribute(WIDTH_SETTLING_ATTR);
      if (anchor?.isConnected) area.scrollTop += anchor.getBoundingClientRect().top - before;
    };
    // A new width changes every row's height. While a pane or window is
    // being resized only the visible rows re-wrap (see beginSettling), and
    // the remembered heights and the repaint layer wait until the width
    // holds still instead of re-rendering the list each frame.
    const settleWidth = () => {
      widthSettle = undefined;
      if (canvasPanesMoving()) return;
      endSettling();
      syncView();
      repaint();
      if (chatKey && area.clientWidth > 0 && noteChatWidth(chatKey, area.clientWidth)) {
        setMeasureGen((n) => n + 1);
      }
    };
    // While the panes move the box changes a little every frame. The
    // overscan covers that, so the rows re-render once at rest
    // (settleWidth) rather than on every frame of the move.
    const syncView = () => setView(prev => {
      const next = {top: area.scrollTop, h: area.clientHeight,
        overscan: Math.min(12000, Math.max(1600, Math.abs(area.scrollTop-prev.top)*3))};
      if (prev.top===next.top && prev.h===next.h) return prev;
      if (canvasPanesMoving() && Math.abs(next.top-prev.top) < MOVE_SLACK && Math.abs(next.h-prev.h) < MOVE_SLACK) return prev;
      return next;
    });
    const sync = () => {
      raf = 0;
      if (area.clientWidth > 0 && area.clientWidth !== lastWidth) {
        lastWidth = area.clientWidth;
        beginSettling();
        clearTimeout(widthSettle);
        widthSettle = setTimeout(settleWidth, WIDTH_SETTLE_MS);
      } else if (!widthSettle && chatKey && area.clientWidth > 0 && noteChatWidth(chatKey, area.clientWidth)) {
        setMeasureGen((n) => n + 1);
      }
      syncView();
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(sync);
      clearTimeout(idle);
      idle = setTimeout(() => setView(prev => prev.overscan===1600 ? prev : {...prev,overscan:1600}), 180);
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    const ro = new ResizeObserver(sync);
    ro.observe(area);
    // A canvas drag may resize this pane from its first frame on. Start
    // settling with it rather than one frame late; it ends at rest.
    const offCanvasMotion = onCanvasMotionChange(() => {
      if (canvasPanesMoving()) beginSettling();
      else if (!widthSettle) widthSettle = setTimeout(settleWidth, WIDTH_SETTLE_MS);
    });
    return () => {
      offCanvasMotion();
      area.removeAttribute(WIDTH_SETTLING_ATTR);
      area.removeEventListener("scroll", onScroll);
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
      cancelAnimationFrame(repaintRaf);
      clearTimeout(idle);
      clearTimeout(widthSettle);
    };
  }, [chatKey, paintRows, areaRef]);
  return {view, notifyMeasured};
}
