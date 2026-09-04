"use client";

import { useEffect, useRef, useState } from "react";
import { jsonFetch } from "@/lib/net/fetch-client";
import type { SelfUpdate, SelfUpdatePage } from "@/lib/self-update";

type History = {
  sessionId: string | null; items: SelfUpdate[]; cursor: string | null;
  loaded: boolean; busy: boolean; stale: boolean; syncedAt: number | null;
};
const empty = (sessionId: string | null): History => ({
  sessionId, items: [], cursor: null, loaded: false, busy: false, stale: false, syncedAt: null,
});

export function useSelfUpdates(sessionId: string | null) {
  const [state, setState] = useState<History>(() => empty(sessionId));
  const more = useRef<() => void>(() => {});
  useEffect(() => {
    let stopped = false;
    let current = empty(sessionId);
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;
    let wantMore = false;
    let pages = 1;
    setState(current);
    if (!sessionId) return;
    const query = new URLSearchParams({ session_id: sessionId, limit: "20" });
    const publish = () => { if (!stopped) setState({ ...current }); };
    async function poll(older = false) {
      if (stopped || request) return;
      clearTimeout(timer);
      const controller = new AbortController();
      request = controller;
      current = { ...current, busy: true };
      publish();
      async function read<T>(url: string): Promise<T> {
        // Per-request timeout; user-loaded pages are refreshed sequentially.
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          const result = await jsonFetch<T>(url, { signal: controller.signal, cache: "no-store" });
          if (controller.signal.aborted) throw new Error("aborted");
          return result;
        } finally { clearTimeout(timeout); }
      }
      try {
        const params = new URLSearchParams(query);
        if (older && current.cursor) params.set("cursor", current.cursor);
        let page = await read<SelfUpdatePage>(`/api/self-updates?${params}`);
        if (page.items.some((item) => item.session_id !== sessionId)) throw new Error("foreign session");
        const items = new Map((older ? current.items : []).map((item) => [item.update_id, item]));
        for (const item of page.items) items.set(item.update_id, item);
        if (!older) {
          // Refresh every user-loaded page, including late terminal receipts.
          // Start a new stable cursor chain so newly inserted history cannot be skipped.
          for (let index = 1; index < pages && page.next_cursor; index++) {
            params.set("cursor", page.next_cursor);
            page = await read<SelfUpdatePage>(`/api/self-updates?${params}`);
            if (page.items.some((item) => item.session_id !== sessionId)) throw new Error("foreign session");
            for (const item of page.items) items.set(item.update_id, item);
          }
        }
        if (stopped) return;
        if (older) pages += 1;
        current = {
          ...current, items: [...items.values()], loaded: true, stale: false, syncedAt: Date.now(),
          cursor: page.next_cursor,
        };
      } catch {
        if (!stopped) current = { ...current, stale: true };
      } finally {
        request = null;
        current = { ...current, busy: false };
        publish();
        if (!stopped) {
          if (wantMore && current.cursor) {
            wantMore = false;
            void poll(true);
          } else timer = setTimeout(() => void poll(), 3000);
        }
      }
    }
    const reconnect = () => { void poll(); };
    more.current = () => {
      if (!current.cursor) return;
      if (request) wantMore = true;
      else void poll(true);
    };
    void poll();
    window.addEventListener("online", reconnect);
    return () => {
      stopped = true;
      more.current = () => {};
      request?.abort();
      clearTimeout(timer);
      window.removeEventListener("online", reconnect);
    };
  }, [sessionId]);
  // A changed prop renders before the effect cleanup; never expose old-session data.
  return { ...(state.sessionId === sessionId ? state : empty(sessionId)), loadMore: () => more.current() };
}
