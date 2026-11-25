"use client";

/**
 * Page changes on the phone move as an iOS navigation stack's do: the new
 * page slides in from the right over the old one, which drifts left and
 * dims; going back plays it in reverse. The earlier push only animated the
 * new page in from half-transparent, with the old one already gone, so
 * for a moment neither was drawn and the change read as a flash.
 *
 * The old page is a copy of its last frame: as React removes it, a static
 * clone (with its scroll positions) takes its place for the length of the
 * slide. The View Transitions API does the same with a snapshot, but
 * WebKit took up to two seconds to capture a long transcript.
 *
 * A swipe back has already moved the page with the finger and changes
 * page without a copy (`skipNextTransition`).
 */

import { useLayoutEffect, useSyncExternalStore, type RefObject } from "react";

const DURATION_MS = 360;

let skip = false;

// Which history entry is showing, counted by the app itself: a push (any
// caller: Next's router, shallow-nav, pushUrl) opens a new page and a pop
// returns to one; a replaceState (a new chat getting its id, a deleted
// chat going to the list) changes the address of the page in place, so
// it neither remounts the page nor slides.
let entry = 0;
let back = false;
const listeners = new Set<() => void>();
function moved(isBack: boolean): void {
  entry += 1;
  back = isBack;
  listeners.forEach((listener) => listener());
}
if (typeof window !== "undefined") {
  const push = window.history.pushState.bind(window.history);
  window.history.pushState = (...args: Parameters<History["pushState"]>) => {
    push(...args);
    moved(false);
  };
  window.addEventListener("popstate", () => moved(true));
}

/** The history entry the app is on; a page slot is keyed by it. */
export function usePageEntry(): number {
  return useSyncExternalStore(
    (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    () => entry,
    () => 0,
  );
}

/** The next page change happens without the slide (it was already dragged). */
export function skipNextTransition(): void {
  skip = true;
}

// The longest the old page stays up waiting for the new one to draw.
const HOLD_MS = 450;

/**
 * Whether the page that came in has drawn what it shows. A conversation
 * mounts first and draws its transcript a few frames later; sliding it in
 * before that showed an empty page that filled in mid-slide.
 */
function drawn(host: Element): boolean {
  const page = host.querySelector(":scope > .phone-app-slot > .phone-app-page");
  if (!page) return false;
  if (!page.classList.contains("phone-conversation") || !window.location.pathname.startsWith("/s/")) return true;
  return !!page.querySelector(".message");
}

/** The scroll offsets of a page's scrolled boxes, in document order. */
function scrolled(page: Element): Array<[number, number, number]> {
  const found: Array<[number, number, number]> = [];
  page.querySelectorAll("*").forEach((el, index) => {
    if (el.scrollTop || el.scrollLeft) found.push([index, el.scrollTop, el.scrollLeft]);
  });
  return found;
}

/**
 * Used by the slot that holds one page: when that page leaves, its copy
 * plays the leaving half of the slide, and the root says which way the
 * pages move (`data-nav` on `.phone-app`, app/styles/base/phone-app.css).
 */
export function useLeavingCopy(slot: RefObject<HTMLElement | null>, root: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const host = root.current;
    const page = slot.current?.firstElementChild;
    return () => {
      if (!host || !page || !host.isConnected) return;
      if (skip || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        skip = false;
        return;
      }
      const offsets = scrolled(page);
      const copy = page.cloneNode(true) as HTMLElement;
      copy.classList.add("phone-app-leaving");
      copy.setAttribute("aria-hidden", "true");
      copy.setAttribute("inert", "");
      const way = back ? "back" : "forward";
      // The copy holds the screen still until the new page has drawn.
      host.setAttribute("data-nav", "hold");
      host.appendChild(copy);
      const nodes = copy.querySelectorAll("*");
      for (const [index, top, left] of offsets) {
        const el = nodes[index];
        if (el) { el.scrollTop = top; el.scrollLeft = left; }
      }
      const started = performance.now();
      const slide = () => {
        if (!copy.isConnected) return;
        if (!drawn(host) && performance.now() - started < HOLD_MS) {
          window.requestAnimationFrame(slide);
          return;
        }
        host.setAttribute("data-nav", way);
        window.setTimeout(() => {
          copy.remove();
          if (!host.querySelector(".phone-app-leaving")) host.removeAttribute("data-nav");
        }, DURATION_MS + 40);
      };
      window.requestAnimationFrame(slide);
    };
  }, [slot, root]);
}
