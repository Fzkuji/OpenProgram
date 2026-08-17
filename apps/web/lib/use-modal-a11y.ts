"use client";

/**
 * Keyboard + screen-reader behaviour for the hand-rolled modals — the
 * ones built from a backdrop <div> instead of `components/ui/dialog`
 * (which is Radix and already does all of this).
 *
 * Gives a panel the three things a modal owes a keyboard user:
 *   1. Escape closes it.
 *   2. Tab is trapped inside the panel, so focus can't wander into the
 *      page behind the backdrop.
 *   3. Focus moves into the panel on open and returns to whatever
 *      triggered it on close.
 *
 * Spread the returned props on the PANEL element (not the backdrop):
 *
 *   const modal = useModalA11y(onClose, label);
 *   <div className="backdrop" onClick={onClose}>
 *     <div {...modal} className="panel" onClick={stopPropagation}> … </div>
 *   </div>
 *
 * ponytail: this is the minimal trap — one Tab-cycle handler over the
 * panel's focusable descendants, no inert/aria-hidden on the rest of
 * the document. Enough for these panels; if a modal ever needs to hide
 * the background from the SR virtual cursor too, move it to the Radix
 * Dialog in components/ui/dialog rather than growing this.
 */

import { useCallback, useEffect, useRef } from "react";
import type React from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function useModalA11y(onClose: () => void, label?: string) {
  const panelRef = useRef<HTMLDivElement>(null);
  // Captured before the panel steals focus, restored on unmount.
  const returnTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    returnTo.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    // Don't fight a panel that already autoFocused one of its own
    // fields — React applies autoFocus before effects run, so honour it.
    if (!panel?.contains(document.activeElement)) {
      // Focus the first control, or the panel itself when it has none —
      // otherwise focus stays on the trigger behind the backdrop and Tab
      // walks the page underneath.
      const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
      (first ?? panel)?.focus();
    }
    return () => returnTo.current?.focus?.();
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const items = Array.from(
        panel.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => el.offsetParent !== null || el === panel);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      // Wrap at both ends so Tab can never leave the panel.
      if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault();
        last.focus();
      }
    },
    [onClose],
  );

  return {
    ref: panelRef,
    role: "dialog" as const,
    "aria-modal": true,
    "aria-label": label,
    // Panels with no focusable child still need somewhere for focus to
    // land, hence the -1 tab stop.
    tabIndex: -1,
    onKeyDown,
  };
}
