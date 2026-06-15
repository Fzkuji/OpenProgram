"use client";

/**
 * `→ /chat` hand-off drain: open a function's fn-form after navigating
 * to a chat route. Two entry points, both consumed by ``takePending``:
 *
 *   * `/chat?run=NAME&cat=CAT` — the primary path. ``FunctionsPage``'s
 *     Use button navigates here; the chat page reads its OWN url and
 *     opens the form (sequential, no global state / event / timing
 *     race). The query is stripped back to `/chat` once consumed so a
 *     refresh doesn't re-fire.
 *
 *   * `window.__pendingRunFunction = { name, cat, fn? }` — the global
 *     stash, kept for ``FavoritesList`` (sidebar) and the inline
 *     ``editProgram`` hop, which set it just before `router.push`.
 *
 * Either way the request is passed to `openFnForm(fn)` once the matching
 * function exists in `window.availableFunctions` — polled up to 30s
 * (fast path ~50ms after the `functions_list` WS envelope).
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

function isChatRoute(p: string): boolean {
  return p === "/chat" || p.startsWith("/s/");
}

/**
 * Re-runs on every `pathname` change. The drain MUST be keyed on the
 * route, not on a constant flag: `<PageShell page="chat">` is mounted
 * once at the layout level and kept alive (hidden) across routes, so a
 * `page === "chat"` boolean never changes and a `[active]` effect would
 * fire only once — missing every `/functions → /chat` hand-off after the
 * first. Keying on `pathname` makes the effect re-fire when the user
 * navigates back onto a chat route, which is exactly when a stashed
 * `__pendingRunFunction` needs draining.
 */
export function usePendingRunFunction(pathname: string): void {
  // Shared drain: take the stashed request and open its form, polling
  // briefly for availableFunctions if needed. Returns a cleanup fn.
  function drain(): (() => void) | void {
    const pending = takePending();
    if (!pending) return;
    let stopped = false;
    // Defer the open: when landing on a fresh /chat, PageShell's
    // pathname effect calls newSession() to reset state — and that runs
    // AFTER this drain effect (effect order = declaration order). If we
    // opened the form synchronously here, newSession() would wipe it a
    // tick later. A 0ms timeout pushes the open past newSession() so the
    // form survives. Then poll for availableFunctions if not ready yet.
    const open = () => {
      if (stopped) return;
      if (tryOpen(pending.name)) return;
      const deadline = Date.now() + 30_000;
      const poll = setInterval(() => {
        if (stopped) { clearInterval(poll); return; }
        if (Date.now() > deadline) {
          clearInterval(poll);
          console.warn(`[run-fn] timeout waiting for ${pending.name}`);
          return;
        }
        if (tryOpen(pending.name)) clearInterval(poll);
      }, 50);
    };
    const t = setTimeout(open, 0);
    return () => { stopped = true; clearTimeout(t); };
  }

  // Drain on route change. Use → router.push("/chat?run=…") always
  // changes pathname (callers are on /functions), so this fires once the
  // chat PageShell mounts; takePending reads the ?run= param. The 30s
  // poll inside drain() covers availableFunctions not being loaded yet.
  useEffect(() => {
    if (!isChatRoute(pathname)) return;
    return drain();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);
}
