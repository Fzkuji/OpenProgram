"use client";

/** The keyboard on an Activity page. One Tab stop among the rows (roving
 *  tabindex, read from the DOM so a poll cannot disturb it), arrows, Home and
 *  End, right and left to open and close, Shift with them between the halves.
 *  Focus never changes the sideways scroll. The listener is on the page's own
 *  element, so a chat on the same screen and dialogs in portals keep their Esc.
 *  A back that answers false had nowhere to go, and leaves Esc to what is around. */
import { useEffect, useRef, type RefObject } from "react";

const motion = (): ScrollBehavior => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
const setStop = (row: HTMLElement, value: "0" | "-1") => { if (row.getAttribute("tabindex") !== value) row.setAttribute("tabindex", value); };
const isTextBox = (target: EventTarget | null) => target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, [contenteditable='true']"));

export function useActivityPageKeys(root: RefObject<HTMLElement | null>, scroller: RefObject<HTMLElement | null>, onBack: () => boolean | void) {
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
    // New rows arrive at tabindex -1; the page keeps exactly one stop, on a
    // row that is drawn: a chart takes the blocks far from the view out of
    // the layout, and Tab would pass over a stop left on one of them.
    const drawn = (row: HTMLElement) => row.style.display !== "none" && (row.parentElement?.style.display ?? "") !== "none";
    const keepOneStop = () => {
      if (held?.isConnected && !held.hasAttribute("disabled") && (drawn(held) || held === document.activeElement)) { setStop(held, "0"); return; }
      const first = rows().find(drawn);
      if (first) rove(first); else held = null;
    };
    keepOneStop();
    const watcher = typeof MutationObserver === "undefined" ? null : new MutationObserver(keepOneStop);
    watcher?.observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ["style"] });

    // The arrows move among the rows that are drawn: a chart keeps the rows
    // of a column out of view out of the layout, and one of those takes no focus.
    const go = (from: HTMLElement | null, step: number | "first" | "last") => {
      const all = rows().filter(row => drawn(row) || row === from);
      const index = step === "first" ? 0 : step === "last" ? all.length - 1 : (from ? all.indexOf(from) : -1) + step;
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
        const typing = isTextBox(event.target);
        if (!typing && back.current() === false) return;
        event.preventDefault();
        event.stopPropagation();
        if (typing) (held ?? rows()[0])?.focus({ preventScroll: true });
        return;
      }
      if (isTextBox(event.target)) return;
      const all = rows();
      const index = all.indexOf(event.target as HTMLElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        go(index < 0 ? null : all[index], event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        go(null, event.key === "Home" ? "first" : "last");
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
