"use client";

/**
 * The phone layout's moving parts (docs/reference/design/ui/phone-layout.html).
 * Everything here is inert above the phone breakpoint: the buttons and the
 * scrim are hidden by app/styles/base/phone.css, and the effects return
 * early. The layout itself is CSS; this file adds what CSS cannot do:
 *
 *  - the scrim that dims the page under the sidebar drawer and closes it;
 *  - the left-edge swipe that drags the drawer open and the swipe on the
 *    drawer that drags it shut, both following the finger;
 *  - the visual viewport's height, so the composer rides above the
 *    on-screen keyboard where the browser does not resize the page;
 *  - closing the drawer and the right panel's sheet once a tab is chosen.
 *
 * Copied from saya-ch/dsh-mobile's dedicated touch layout (Apache-2.0).
 */

import { useEffect, useRef } from "react";
import { PanelLeftOpenIcon } from "../animated-icons";
import { sidebarToggleClass } from "../sidebar/nav-classes";
import { useTranslation } from "@/lib/i18n";
import { usePhoneViewport } from "@/lib/layout/phone";
import { sidebarDrawer } from "@/lib/layout/sidebar-drawer";
import { useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";

/** How far in from the left edge a touch may start an opening swipe. */
const EDGE_PX = 24;
/** Travel before a touch is read as a horizontal drag or a scroll. */
const DECIDE_PX = 8;
/** A release faster than this (px/ms) settles in its own direction. */
const FLING = 0.4;

function drawerElement(): HTMLElement | null {
  return document.getElementById("sidebar");
}

/** The left-edge swipe and the drawer's own swipe, following the finger. */
function useDrawerSwipe(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let track: {
      opening: boolean;
      startX: number;
      startY: number;
      width: number;
      decided: boolean;
      offset: number;
      lastX: number;
      lastT: number;
      velocity: number;
    } | null = null;
    const scrim = () => document.querySelector<HTMLElement>(".phone-scrim");

    const onStart = (event: TouchEvent) => {
      track = null;
      if (event.touches.length !== 1) return;
      const el = drawerElement();
      if (!el) return;
      const touch = event.touches[0];
      const open = sidebarDrawer.isOpen();
      const target = event.target as Element | null;
      const opening = !open && touch.clientX <= EDGE_PX;
      const closing = open && !!target && (el.contains(target) || target.classList?.contains("phone-scrim"));
      if (!opening && !closing) return;
      track = {
        opening,
        startX: touch.clientX,
        startY: touch.clientY,
        width: el.getBoundingClientRect().width || 1,
        decided: false,
        offset: opening ? -Infinity : 0,
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
        if (Math.abs(dy) > Math.abs(dx) || (track.opening ? dx < 0 : dx > 0)) {
          track = null;
          return;
        }
        track.decided = true;
        drawerElement()?.setAttribute("data-dragging", "");
        scrim()?.setAttribute("data-dragging", "");
      }
      event.preventDefault();
      const dt = Math.max(1, event.timeStamp - track.lastT);
      track.velocity = (touch.clientX - track.lastX) / dt;
      track.lastX = touch.clientX;
      track.lastT = event.timeStamp;
      const offset = Math.max(-track.width, Math.min(0, track.opening ? dx - track.width : dx));
      track.offset = offset;
      const el = drawerElement();
      if (el) el.style.transform = `translateX(${offset}px)`;
      const s = scrim();
      if (s) s.style.opacity = String(1 + offset / track.width);
    };

    const onEnd = () => {
      const done = track;
      track = null;
      if (!done?.decided) return;
      const el = drawerElement();
      const s = scrim();
      const shown = 1 + done.offset / done.width;
      const open = Math.abs(done.velocity) > FLING ? done.velocity > 0 : shown > 0.5;
      // Hand the drawer back to its classes; the CSS transition carries it
      // from where the finger left it to where it settles.
      el?.removeAttribute("data-dragging");
      s?.removeAttribute("data-dragging");
      if (el) el.style.transform = "";
      if (s) s.style.opacity = "";
      if (open !== sidebarDrawer.isOpen()) {
        if (open) sidebarDrawer.open();
        else sidebarDrawer.close();
      }
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
    const write = () => {
      frame = 0;
      root.style.setProperty("--phone-viewport-h", `${Math.round(viewport.height)}px`);
      root.style.setProperty("--phone-viewport-top", `${Math.round(viewport.offsetTop)}px`);
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
    };
  }, [enabled]);
}

/** Choosing a tab shows it: the drawer and the right panel's sheet close. */
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

/** Mounted once by AppShell: the drawer's scrim and the phone effects. */
export function PhoneShell() {
  const phone = usePhoneViewport();
  useDrawerSwipe(phone);
  useVisualViewportVars(phone);
  useCloseOverlaysOnTabChange(phone);
  return (
    <div
      className="phone-scrim"
      aria-hidden="true"
      onClick={() => sidebarDrawer.close()}
    />
  );
}

/** The header button that opens the right panel as a full-screen sheet
 *  (phone only); the sheet's own toggle closes it. */
export function PhonePanelButton() {
  const { text } = useTranslation();
  const setRightDockOpen = useSessionStore((s) => s.setRightDockOpen);
  return (
    <button
      type="button"
      className={`${sidebarToggleClass} phone-only phone-header-button`}
      aria-label={text("Show panel", "显示面板")}
      onClick={() => setRightDockOpen(true)}
    >
      <PanelLeftOpenIcon size={20} style={{ transform: "scaleX(-1)" }} />
    </button>
  );
}
