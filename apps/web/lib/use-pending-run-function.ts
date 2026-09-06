"use client";

/** Open a requested Program after the chat route has completed its reset. */
import { useEffect } from "react";
import { openFunctionForm } from "@/lib/state/functions-actions";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { newSession } from "@/lib/runtime-bridge/conversations";

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

export function usePendingRunFunction(pathname: string, ready = true): void {
  useEffect(() => {
    if (pathname !== "/chat" && !pathname.startsWith("/s/")) return;
    // The route reset can clear the query before initialization finishes.
    // Retain the request now; consume it only once the chat is ready.
    const params = new URLSearchParams(window.location.search);
    const name = params.get("run");
    if (name) setPendingRunFunction({ name, cat: params.get("cat") || "" });
    if (!ready) return;
    const controller = new AbortController();
    // Defer both consumption and opening past the chat reset. Strict Mode's
    // discarded setup must not consume a request that its cleanup cancels.
    const timer = setTimeout(() => {
      const request = takePending();
      if (!request) return;
      if (pathname === "/chat") {
        // Explicit Program navigation starts a draft; startup restoration alone
        // must not leave the previous persisted tab owning this blank chat.
        const tabs = useCenterTabs.getState();
        const active = tabs.tabs.find((tab) => tab.id === tabs.activeId);
        const draftId = active?.kind === "session" && active.draft && active.sessionId
          ? active.sessionId
          : tabs.openDraftSessionTab();
        newSession(draftId);
      }
      void openFunctionForm(request.name, controller.signal);
    }, 0);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [pathname, ready]);
}
