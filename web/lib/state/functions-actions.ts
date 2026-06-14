"use client";

/**
 * Functions-related actions that don't fit a component (one-shot
 * fetches, store writes that need to be triggered from event handlers
 * rather than render). The matching mutations live in
 * `lib/functions-store.ts`; this file just wraps the network calls and
 * keeps the legacy `window.availableFunctions` mirror in sync while
 * the WS reducer still feeds the legacy globals.
 */

import { useFunctions } from "./functions-store";
import type { AgenticFunction } from "@/lib/session-store";

interface LegacyMirror {
  availableFunctions?: AgenticFunction[];
  refreshFunctions?: () => Promise<void>;
}

/**
 * Re-fetch the function catalogue from `/api/functions` and publish
 * it to both the React store (`useFunctions.setFunctions`) and the
 * legacy global (`window.availableFunctions`). Mirrors the legacy
 * `refreshFunctions` in functions-panel.ts so the sidebar refresh
 * button no longer needs to go through `window.refreshFunctions`.
 */
export async function refreshFunctionsList(): Promise<void> {
  try {
    const resp = await fetch("/api/functions");
    const data: AgenticFunction[] = await resp.json();
    const fns = Array.isArray(data) ? data : [];
    useFunctions.getState().setFunctions(fns);
    (window as unknown as LegacyMirror).availableFunctions = fns;
  } catch (err) {
    console.error("Refresh functions failed:", err);
  }
}
