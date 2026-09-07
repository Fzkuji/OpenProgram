"use client";

import { useEffect, useState } from "react";
import { jsonFetch } from "./net/fetch-client";
import { backendResourceRows, type BackendResource, type SessionResource } from "./state/session-resources";

/** Poll only while the picker is open; each request is independently session-authorized. */
export function useSessionResources(sessionKey: string) {
  const [rows, setRows] = useState<SessionResource[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const sessions: string[] = JSON.parse(sessionKey);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController;
    const previous = new Map<string, SessionResource[]>();
    setRows([]); setLoaded(false); setUnavailable(false);
    async function poll() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      const results = await Promise.allSettled(sessions.map(async sessionId => {
        const data = await jsonFetch<{ items: BackendResource[] }>(`/api/session/${encodeURIComponent(sessionId)}/resources`, {
          signal: controller.signal, cache: "no-store",
        });
        return backendResourceRows(data.items, sessionId);
      }));
      clearTimeout(timeout);
      if (disposed) return;
      results.forEach((result, index) => {
        if (result.status === "fulfilled") previous.set(sessions[index], result.value);
      });
      setRows([...previous.values()].flat());
      setUnavailable(results.some(result => result.status === "rejected"));
      setLoaded(true);
      timer = setTimeout(poll, 3000);
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); };
  }, [sessionKey]);
  return { rows, unavailable, loaded };
}
