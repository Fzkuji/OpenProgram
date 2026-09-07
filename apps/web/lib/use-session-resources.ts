"use client";

import { useEffect, useState } from "react";
import { jsonFetch } from "./net/fetch-client";
import { backendResourceRows, type BackendResource, type SessionResource } from "./state/session-resources";

/** Mounted by a session-keyed panel; never poll another open conversation. */
export function useSessionResources(sessionId: string | null) {
  const [rows, setRows] = useState<SessionResource[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (!sessionId) { setLoaded(true); return; }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController;
    async function poll() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const data = await jsonFetch<{ items: BackendResource[] }>(`/api/session/${encodeURIComponent(sessionId!)}/resources`, {
          signal: controller.signal, cache: "no-store",
        });
        if (!disposed) {
          setRows(backendResourceRows(data.items, sessionId!));
          setUnavailable(false);
        }
      } catch {
        if (!disposed) setUnavailable(true);
      } finally {
        clearTimeout(timeout);
      }
      if (disposed) return;
      setLoaded(true);
      timer = setTimeout(poll, 3000);
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); };
  }, [sessionId]);
  return { rows, unavailable, loaded };
}
