"use client";

/** The keyboard on an Activity page. One Tab stop among the rows (roving
 *  tabindex, read from the DOM so a poll cannot disturb it), arrows, Home and
 *  End, right and left to open and close, Shift with them between the halves.
 *  Focus never changes the sideways scroll. The listener is on the page's own
 *  element, so a chat on the same screen and dialogs in portals keep their Esc. */
import { useEffect, useRef, type RefObject } from "react";

const motion = (): ScrollBehavior => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
const setStop = (row: HTMLElement, value: "0" | "-1") => { if (row.getAttribute("tabindex") !== value) row.setAttribute("tabindex", value); };
const isTextBox = (target: EventTarget | null) => target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, [contenteditable='true']"));

export function useActivityPageKeys(root: RefObject<HTMLElement | null>, scroller: RefObject<HTMLElement | null>, onBack: () => void) {
  const back = useRef(onBack);
  useEffect(() => { back.current = onBack; }, [onBack]);
  useEffect(() => {
    const page = root.current;
    const list = scroller.current;
    if (!page || !list) return;
    const rows = () => [...list.querySelectorAll<HTMLElement>("[data-row-id]")];
    let held: HTMLElement | null = null;
    const rove = (target: HTMLElement) => {
      if (held && held !== target) setStop(held, "-1");
      setStop(target, "0");
      held = target;
    };
    // New rows arrive at tabindex -1; the page keeps exactly one stop.
    const keepOneStop = () => {
      if (held?.isConnected && !held.hasAttribute("disabled")) { setStop(held, "0"); return; }
      const first = rows()[0];
      if (first) rove(first); else held = null;
    };
    keepOneStop();
    const watcher = typeof MutationObserver === "undefined" ? null : new MutationObserver(keepOneStop);
    watcher?.observe(list, { childList: true, subtree: true });

    const go = (index: number) => {
      const all = rows();
      const target = all[Math.max(0, Math.min(all.length - 1, index))];
      if (!target) return;
      const left = list.scrollLeft;
      rove(target);
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "nearest", inline: "nearest" });
      list.scrollLeft = left;
    };

    let tabLeft: number | null = null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Tab") { tabLeft = list.scrollLeft; return; }
      if (event.key === "Escape") {
        if (event.defaultPrevented) return;
        event.preventDefault();
        event.stopPropagation();
        if (isTextBox(event.target)) { (held ?? rows()[0])?.focus({ preventScroll: true }); return; }
        back.current();
        return;
      }
      if (isTextBox(event.target)) return;
      const all = rows();
      const index = all.indexOf(event.target as HTMLElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        go((index < 0 ? -1 : index) + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        go(event.key === "Home" ? 0 : all.length - 1);
      } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        if (event.shiftKey) {
          event.preventDefault();
          list.scrollTo({ left: event.key === "ArrowRight" ? list.scrollWidth : 0, behavior: motion() });
          return;
        }
        if (index < 0) return;
        const expanded = all[index].getAttribute("aria-expanded");
        if (expanded !== null && (event.key === "ArrowRight") === (expanded === "false")) {
          event.preventDefault();
          all[index].click();
        }
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      const row = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>("[data-row-id]") : null;
      if (row && list.contains(row)) rove(row);
      if (tabLeft === null) return;
      const left = tabLeft;
      tabLeft = null;
      requestAnimationFrame(() => { list.scrollLeft = left; });
    };
    page.addEventListener("keydown", onKeyDown);
    page.addEventListener("focusin", onFocusIn);
    return () => {
      watcher?.disconnect();
      page.removeEventListener("keydown", onKeyDown);
      page.removeEventListener("focusin", onFocusIn);
    };
  }, [root, scroller]);
}
