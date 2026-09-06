"use client";

/** Open a requested Program after the chat route has completed its reset. */
import { useEffect } from "react";
import { openFunctionForm } from "@/lib/state/functions-actions";

export interface PendingRunFunction {
  name: string;
  cat?: string;
  fn?: string;
}

let pending: PendingRunFunction | null = null;

/** Stash a fn-form request to be drained on the next chat route. */
export function setPendingRunFunction(req: PendingRunFunction): void {
  pending = req;
}

/** Take and clear the stash (single-shot). */
export function takePendingRunFunction(): PendingRunFunction | null {
  const stash = pending;
  pending = null;
  return stash;
}

function takePending(): { name: string; cat: string } | null {
  const stash = takePendingRunFunction();
  if (stash && stash.name) {
    return { name: stash.name, cat: stash.cat || "" };
  }
  const params = new URLSearchParams(window.location.search);
  const runName = params.get("run");
  const runCat = params.get("cat") || "";
  if (!runName) return null;
  history.replaceState(null, "", "/chat");
  return { name: runName, cat: runCat };
}

export function usePendingRunFunction(pathname: string): void {
  useEffect(() => {
    if (pathname !== "/chat" && !pathname.startsWith("/s/")) return;
    const controller = new AbortController();
    // Defer both consumption and opening past the chat reset. Strict Mode's
    // discarded setup must not consume a request that its cleanup cancels.
    const timer = setTimeout(() => {
      const request = takePending();
      if (request) void openFunctionForm(request.name, controller.signal);
    }, 0);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [pathname]);
}
