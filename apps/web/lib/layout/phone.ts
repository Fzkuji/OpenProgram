"use client";

import { useSyncExternalStore } from "react";

/**
 * The phone layout's one breakpoint. At or below this width the shell is a
 * single column: the sidebar is a drawer over the page, the right panel a
 * full-screen sheet, and a page's secondary rail a list page of its own
 * (app/styles/base/phone.css, docs/reference/design/ui/phone-layout.html).
 * CSS cannot import it, so phone.css writes the same 760px.
 */
export const PHONE_MAX_WIDTH = 760;
export const PHONE_QUERY = `(max-width: ${PHONE_MAX_WIDTH}px)`;

export function isPhoneViewport(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.(PHONE_QUERY).matches === true;
}

function subscribe(onChange: () => void): () => void {
  const query = typeof window === "undefined" ? null : window.matchMedia?.(PHONE_QUERY);
  if (!query) return () => {};
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** Whether the window is in the phone layout, following resizes. */
export function usePhoneViewport(): boolean {
  return useSyncExternalStore(subscribe, isPhoneViewport, () => false);
}
