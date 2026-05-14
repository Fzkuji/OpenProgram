"use client";

/**
 * `/programs → /chat` hand-off drain.
 *
 * Two entry points (both kept for parity with the legacy
 * `__triggerPendingRunFunction` in init.js that this replaces):
 *
 *   * `window.__pendingRunFunction = { name, cat }` — set by
 *     `<FavoritesList />` / `<ProgramsPage />` just before
 *     `router.push("/chat")`. Survives the SPA hop because
 *     `router.push` doesn't re-run any `<script>` top-level.
 *
 *   * `/chat?run=NAME&cat=CAT` — hard-refresh / direct link into
 *     the chat route. We strip the query string back to `/chat`
 *     once consumed so a subsequent reload doesn't re-trigger.
 *
 * Either entry point gets passed to `openFnForm(fn)` once the
 * matching function exists in `window.availableFunctions`. The
 * functions list is still populated by the legacy WS handler in
 * init.js; we poll for it up to 30s — typical fast path is
 * ~50ms after the `functions_list` envelope.
 */

import { useEffect } from "react";

interface WindowShim {
  __pendingRunFunction?: { name: string; cat?: string } | null;
  availableFunctions?: { name: string }[];
  __sessionStore?: {
    getState: () => { openFnForm: (fn: unknown) => void };
  };
}

function takePending(): { name: string; cat: string } | null {
  const w = window as unknown as WindowShim;
  const stash = w.__pendingRunFunction;
  if (stash && stash.name) {
    w.__pendingRunFunction = null;
    return { name: stash.name, cat: stash.cat || "" };
  }
  const params = new URLSearchParams(window.location.search);
  const runName = params.get("run");
  const runCat = params.get("cat") || "";
  if (!runName) return null;
  history.replaceState(null, "", "/chat");
  return { name: runName, cat: runCat };
}

function tryOpen(name: string): boolean {
  const w = window as unknown as WindowShim;
  const fns = w.availableFunctions ?? [];
  if (fns.length === 0) return false;
  const fn = fns.find((f) => f.name === name);
  if (!fn) return false;
  const store = w.__sessionStore;
  if (!store) return false;
  store.getState().openFnForm(fn);
  return true;
}

/**
 * Fires on `active` rising edges (chat route mount + each pathname
 * change while on a chat route). Cleans up its own poll on unmount.
 */
export function usePendingRunFunction(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const pending = takePending();
    if (!pending) return;
    if (tryOpen(pending.name)) return;

    let stopped = false;
    const deadline = Date.now() + 30_000;
    const poll = setInterval(() => {
      if (stopped) return;
      if (Date.now() > deadline) {
        clearInterval(poll);
        console.warn(`[?run] timeout waiting for ${pending.name}`);
        return;
      }
      if (tryOpen(pending.name)) clearInterval(poll);
    }, 50);
    return () => {
      stopped = true;
      clearInterval(poll);
    };
  }, [active]);
}
