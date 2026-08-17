"use client";

/**
 * Functions-related actions that don't fit a component (one-shot
 * fetches, store writes that need to be triggered from event handlers
 * rather than render). The matching mutations live in
 * `lib/functions-store.ts`; this file just wraps the network calls and
 * keeps `runtimeState.availableFunctions` in sync while the WS reducer
 * still feeds the shared runtime state.
 */

import { useFunctions } from "./functions-store";
import { runtimeState } from "@/lib/runtime-bridge/state";
import type { AgenticFunction } from "@/lib/session-store";

/**
 * Re-fetch the function catalogue from `/api/programs` and publish
 * it to both the React store (`useFunctions.setFunctions`) and
 * `runtimeState.availableFunctions` (read by the `→ /chat` fn-form
 * hand-off in `lib/use-pending-run-function.ts`). Mirrors
 * `refreshFunctions` in functions-panel.ts so the sidebar refresh
 * button no longer needs to go through a global.
 */
export async function refreshFunctionsList(): Promise<void> {
  try {
    const resp = await fetch("/api/programs");
    const data: AgenticFunction[] = await resp.json();
    const fns = Array.isArray(data) ? data : [];
    useFunctions.getState().setFunctions(fns);
    runtimeState.availableFunctions = fns;
  } catch (err) {
    console.error("Refresh functions failed:", err);
  }
}
