"use client";

/**
 * A swipe from the left edge goes one page back, the page following the
 * finger as an iOS navigation stack's does. In the store app only: a
 * phone's browser has its own edge gesture, and both would go back.
 * WKWebView's own back-forward gesture does not take the history entries
 * the pages push, so the app does not turn it on (WebShell.swift).
 */

import { useEffect, type RefObject } from "react";

import { mobileBridge } from "@/lib/mobile/bridge";

import { skipNextTransition } from "./use-page-transitions";

const EDGE = 24; // px from the left edge where a swipe may start
const COMMIT = 0.33; // of the width, or a flick

export function useEdgeSwipeBack(root: RefObject<HTMLElement | null>, canGoBack: boolean): void {
  useEffect(() => {
    const el = root.current;
    if (!el || !canGoBack || !mobileBridge()) return;
    let page: HTMLElement | null = null;
    let startX = 0;
    let startY = 0;
    let lastX = 0;
    let lastT = 0;
    let velocity = 0;
    let dragging = false;

    const onStart = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (event.touches.length !== 1 || touch.clientX > EDGE) return;
      page = el.querySelector<HTMLElement>(":scope > .phone-app-slot > .phone-app-page");
      if (!page) return;
      startX = lastX = touch.clientX;
      startY = touch.clientY;
      lastT = event.timeStamp;
      velocity = 0;
      dragging = false;
    };
    const onMove = (event: TouchEvent) => {
      if (!page) return;
      const touch = event.touches[0];
      const dx = touch.clientX - startX;
      if (!dragging) {
        // A vertical start is a scroll, not a swipe back.
        if (Math.abs(touch.clientY - startY) > Math.abs(dx)) { page = null; return; }
        if (dx < 6) return;
        dragging = true;
        page.style.transition = "none";
        page.style.animation = "none";
      }
      event.preventDefault();
      const dt = Math.max(1, event.timeStamp - lastT);
      velocity = (touch.clientX - lastX) / dt;
      lastX = touch.clientX;
      lastT = event.timeStamp;
      page.style.transform = `translateX(${Math.max(0, dx)}px)`;
    };
    const onEnd = () => {
      const target = page;
      page = null;
      if (!target || !dragging) return;
      dragging = false;
      const width = el.clientWidth || window.innerWidth;
      const dx = lastX - startX;
      const commit = dx > width * COMMIT || velocity > 0.5;
      target.style.transition = "transform 220ms cubic-bezier(0.32, 0.72, 0, 1)";
      target.style.transform = commit ? `translateX(${width}px)` : "";
      const done = () => {
        target.style.transition = "";
        if (commit) { skipNextTransition(); window.history.back(); }
        else { target.style.transform = ""; target.style.animation = ""; }
      };
      target.addEventListener("transitionend", done, { once: true });
    };

    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
    };
  }, [root, canGoBack]);
}
