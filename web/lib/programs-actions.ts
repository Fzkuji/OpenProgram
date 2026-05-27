"use client";

/**
 * Programs-related actions that don't fit a component (one-shot
 * fetches, store writes that need to be triggered from event handlers
 * rather than render). The matching mutations live in
 * `lib/programs-store.ts`; this file just wraps the network calls and
 * keeps the legacy `window.availableFunctions` mirror in sync while
 * the WS reducer still feeds the legacy globals.
 */

import { usePrograms } from "./programs-store";
import type { AgenticFunction } from "./session-store";

interface LegacyMirror {
  availableFunctions?: AgenticFunction[];
  refreshFunctions?: () => Promise<void>;
}

/**
 * Re-fetch the function catalogue from `/api/functions` and publish
 * it to both the React store (`usePrograms.setFunctions`) and the
 * legacy global (`window.availableFunctions`). Mirrors the legacy
 * `refreshFunctions` in programs-panel.js so the sidebar refresh
 * button no longer needs to go through `window.refreshFunctions`.
 */
export async function refreshFunctionsList(): Promise<void> {
  try {
    const resp = await fetch("/api/functions");
    const data: AgenticFunction[] = await resp.json();
    const fns = Array.isArray(data) ? data : [];
    usePrograms.getState().setFunctions(fns);
    (window as unknown as LegacyMirror).availableFunctions = fns;
  } catch (err) {
    console.error("Refresh functions failed:", err);
  }
}
