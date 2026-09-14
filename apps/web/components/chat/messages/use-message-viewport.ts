"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { noteChatWidth } from "@/lib/chat/message-window";

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
    let idle: ReturnType<typeof setTimeout> | undefined;
    const sync = () => {
      raf = 0;
      if (chatKey && area.clientWidth > 0 && noteChatWidth(chatKey, area.clientWidth)) {
        setMeasureGen((n) => n + 1);
      }
      setView(prev => {
        const next = {top: area.scrollTop, h: area.clientHeight,
          overscan: Math.min(12000, Math.max(1600, Math.abs(area.scrollTop-prev.top)*3))};
        return prev.top===next.top && prev.h===next.h ? prev : next;
      });
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(sync);
      clearTimeout(idle);
      idle = setTimeout(() => setView(prev => prev.overscan===1600 ? prev : {...prev,overscan:1600}), 180);
    };
    area.addEventListener("scroll", onScroll, { passive: true });
    const ro = new ResizeObserver(sync);
    ro.observe(area);
    return () => {
      area.removeEventListener("scroll", onScroll);
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
      clearTimeout(idle);
    };
  }, [chatKey, paintRows, areaRef]);
  return {view, notifyMeasured};
}
