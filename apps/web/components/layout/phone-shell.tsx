"use client";

/**
 * The phone layout's moving parts (docs/reference/design/ui/phone-layout.html).
 * Everything here is inert above the phone breakpoint: the buttons and the
 * scrim are hidden by app/styles/base/phone.css, and the effects return
 * early. The layout itself is CSS; this file adds what CSS cannot do:
 *
 *  - the scrim that dims the page under either drawer and closes it;
 *  - the edge swipes: from the left edge the sidebar, from the right edge
 *    the right panel, each following the finger, and the swipe on an open
 *    drawer that drags it shut;
 *  - the visual viewport's height, so the composer rides above the
 *    on-screen keyboard where the browser does not resize the page;
 *  - closing both drawers once a tab is chosen;
 *  - one page at a time on a touch phone: opening a page closes the one
 *    before it, so the tabs never pile up where no tab row shows them.
 *
 * Copied from saya-ch/dsh-mobile's dedicated touch layout (Apache-2.0).
 */

import { useEffect, useRef } from "react";
import { usePhoneViewport, useTouchPhone } from "@/lib/layout/phone";
import { sidebarDrawer } from "@/lib/layout/sidebar-drawer";
import { useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";

/** How far in from the left edge a touch may start an opening swipe. */
const EDGE_PX = 24;
/** Travel before a touch is read as a horizontal drag or a scroll. */
const DECIDE_PX = 8;
/** A release faster than this (px/ms) settles in its own direction. */
const FLING = 0.4;

/** One side's drawer: the element, and its open state. */
interface Drawer {
  side: "left" | "right";
  element: () => HTMLElement | null;
  isOpen: () => boolean;
  setOpen: (open: boolean) => void;
}

const DRAWERS: Drawer[] = [
  {
    side: "left",
    element: () => document.getElementById("sidebar"),
    isOpen: () => sidebarDrawer.isOpen(),
    setOpen: (open) => (open ? sidebarDrawer.open() : sidebarDrawer.close()),
  },
  {
    side: "right",
    element: () => document.getElementById("rightSidebar"),
    isOpen: () => useSessionStore.getState().rightDock.open,
    setOpen: (open) => useSessionStore.getState().setRightDockOpen(open),
  },
];

/** The edge swipes that drag either drawer open and the swipe on an open
 *  drawer (or the scrim) that drags it shut, both following the finger. */
function useDrawerSwipe(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let track: {
      drawer: Drawer;
      el: HTMLElement;
      /** +1 when the drawer opens rightwards (left drawer), -1 otherwise. */
      dir: 1 | -1;
      opening: boolean;
      startX: number;
      startY: number;
      width: number;
      decided: boolean;
      /** How much of the drawer shows, 0..1. */
      shown: number;
      lastX: number;
      lastT: number;
      velocity: number;
    } | null = null;
    const scrim = () => document.querySelector<HTMLElement>(".phone-scrim");

    const onStart = (event: TouchEvent) => {
      track = null;
      if (event.touches.length !== 1) return;
      const touch = event.touches[0];
      const target = event.target as Element | null;
      const onScrim = !!target?.classList?.contains("phone-scrim");
      const width = window.innerWidth;
      const open = DRAWERS.find((d) => d.isOpen());
      let drawer: Drawer | undefined;
      let opening = false;
      if (open) {
        const el = open.element();
        if (el && target && (el.contains(target) || onScrim)) drawer = open;
      } else if (touch.clientX <= EDGE_PX) {
        drawer = DRAWERS[0];
        opening = true;
      } else if (touch.clientX >= width - EDGE_PX) {
        drawer = DRAWERS[1];
        opening = true;
      }
      const el = drawer?.element();
      if (!drawer || !el) return;
      track = {
        drawer,
        el,
        dir: drawer.side === "left" ? 1 : -1,
        opening,
        startX: touch.clientX,
        startY: touch.clientY,
        width: el.getBoundingClientRect().width || 1,
        decided: false,
        shown: opening ? 0 : 1,
        lastX: touch.clientX,
        lastT: event.timeStamp,
        velocity: 0,
      };
    };

    const onMove = (event: TouchEvent) => {
      if (!track) return;
      const touch = event.touches[0];
      const dx = touch.clientX - track.startX;
      const dy = touch.clientY - track.startY;
      if (!track.decided) {
        if (Math.abs(dx) < DECIDE_PX && Math.abs(dy) < DECIDE_PX) return;
        // A vertical move is the list scrolling; a swipe the wrong way is not ours.
        const toward = dx * track.dir;
        if (Math.abs(dy) > Math.abs(dx) || (track.opening ? toward < 0 : toward > 0)) {
          track = null;
          return;
        }
        track.decided = true;
        track.el.setAttribute("data-dragging", "");
        scrim()?.setAttribute("data-dragging", "");
      }
      event.preventDefault();
      const dt = Math.max(1, event.timeStamp - track.lastT);
      track.velocity = ((touch.clientX - track.lastX) / dt) * track.dir;
      track.lastX = touch.clientX;
      track.lastT = event.timeStamp;
      const moved = (dx * track.dir) / track.width;
      const shown = Math.max(0, Math.min(1, (track.opening ? 0 : 1) + moved));
      track.shown = shown;
      track.el.style.transform = `translateX(${-track.dir * (1 - shown) * track.width}px)`;
      track.el.style.visibility = "visible";
      const s = scrim();
      if (s) s.style.opacity = String(shown);
    };

    const onEnd = () => {
      const done = track;
      track = null;
      if (!done?.decided) return;
      const open = Math.abs(done.velocity) > FLING ? done.velocity > 0 : done.shown > 0.5;
      // Settle the state first while the finger's transform still holds the
      // drawer; once the drawer's classes say where it belongs, hand it back
      // to them, so the CSS transition carries it on from where the finger
      // left it instead of jumping to its old place first.
      if (open !== done.drawer.isOpen()) done.drawer.setOpen(open);
      let frames = 0;
      const release = () => {
        const settled = done.el.classList.contains("collapsed") !== open;
        if (!settled && ++frames < 12) {
          window.requestAnimationFrame(release);
          return;
        }
        const s = scrim();
        done.el.removeAttribute("data-dragging");
        s?.removeAttribute("data-dragging");
        done.el.style.transform = "";
        done.el.style.visibility = "";
        if (s) s.style.opacity = "";
      };
      window.requestAnimationFrame(release);
    };

    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onEnd);
    document.addEventListener("touchcancel", onEnd);
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onEnd);
      document.removeEventListener("touchcancel", onEnd);
    };
  }, [enabled]);
}

/** The visual viewport's height and top, for a shell pinned above the
 *  on-screen keyboard where the browser shrinks only the visual viewport. */
function useVisualViewportVars(enabled: boolean) {
  useEffect(() => {
    const viewport = window.visualViewport;
    const root = document.documentElement;
    if (!enabled || !viewport) return;
    let frame = 0;
    // The viewport's height without the keyboard: the tallest seen at
    // this width (a rotation starts over).
    let full = 0;
    let width = 0;
    const write = () => {
      frame = 0;
      if (viewport.width !== width) {
        width = viewport.width;
        full = 0;
      }
      full = Math.max(full, viewport.height);
      root.style.setProperty("--phone-viewport-h", `${Math.round(viewport.height)}px`);
      root.style.setProperty("--phone-viewport-top", `${Math.round(viewport.offsetTop)}px`);
      // The keyboard covers the home indicator, so the page drops the
      // bottom safe area while it is up (phone.css).
      root.toggleAttribute("data-phone-keyboard", full - viewport.height > 120);
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(write);
    };
    write();
    viewport.addEventListener("resize", schedule);
    viewport.addEventListener("scroll", schedule);
    return () => {
      viewport.removeEventListener("resize", schedule);
      viewport.removeEventListener("scroll", schedule);
      if (frame) window.cancelAnimationFrame(frame);
      root.style.removeProperty("--phone-viewport-h");
      root.style.removeProperty("--phone-viewport-top");
      root.removeAttribute("data-phone-keyboard");
    };
  }, [enabled]);
}

/** Choosing a tab shows it: the drawer and the right panel's drawer close. */
function useCloseOverlaysOnTabChange(enabled: boolean) {
  const activeId = useCenterTabs((s) => s.activeId);
  const last = useRef(activeId);
  useEffect(() => {
    if (last.current === activeId) return;
    last.current = activeId;
    if (!enabled) return;
    sidebarDrawer.close();
    const store = useSessionStore.getState();
    if (store.rightDock.open) store.setRightDockOpen(false);
  }, [activeId, enabled]);
}

/** A narrow desktop window keeps its tabs; only a touch phone (the app, a
 *  phone's browser) is held to one. */
/** On a touch phone the open page is the only tab: whenever another is
 *  opened (a chat from the drawer, a new chat, a file), the rest close. */
function useSingleTab(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const trim = (state: ReturnType<typeof useCenterTabs.getState>) => {
      if (state.tabs.length <= 1 || !state.activeId) return;
      for (const tab of state.tabs) {
        if (tab.id !== state.activeId) state.closeTab(tab.id);
      }
    };
    trim(useCenterTabs.getState());
    return useCenterTabs.subscribe(trim);
  }, [enabled]);
}

/** Mounted once by AppShell: the drawer's scrim and the phone effects.
 *  The phone app (components/phone/phone-app.tsx) has no drawers: it keeps
 *  the keyboard-aware height and one tab. */
export function PhoneShell({ drawers = true }: { drawers?: boolean }) {
  const phone = usePhoneViewport();
  useDrawerSwipe(phone && drawers);
  useVisualViewportVars(phone);
  useCloseOverlaysOnTabChange(phone && drawers);
  useSingleTab(useTouchPhone());
  if (!drawers) return null;
  return (
    <div
      className="phone-scrim"
      aria-hidden="true"
      onClick={() => {
        sidebarDrawer.close();
        useSessionStore.getState().setRightDockOpen(false);
      }}
    />
  );
}
