"use client";

import { useEffect, useRef, useState } from "react";
import { SystemAccessRecovery } from "./system-access-recovery";
import {
  forgetSystemAccessWait,
  systemAccessWaitHandled,
  markSystemAccessWaitHandled,
  rememberedSystemAccessWaits,
  rememberSystemAccessWait,
  takeLiveSystemAccessWaits,
  type SystemAccessWait,
} from "@/lib/access/system-access-wait-state";

type AccessWait = SystemAccessWait;

/** This is a projection of worker-owned waits. It never dispatches a task. */
export function SystemAccessWaits({ sessionId }: { sessionId: string | null }) {
  const [waits, setWaits] = useState<AccessWait[]>([]);
  const live = useRef(new Set<string>());
  const verified = useRef(new Set<string>());
  const hasWaits = useRef(false);
  hasWaits.current = waits.length > 0;
  useEffect(() => {
    const restored = sessionId ? rememberedSystemAccessWaits(sessionId) : [];
    const liveRestored = sessionId ? takeLiveSystemAccessWaits(sessionId) : [];
    setWaits(restored);
    live.current.clear();
    verified.current.clear();
    for (const wait of liveRestored) live.current.add(wait.wait_id);
    if (!sessionId) return;
    const sid = sessionId;
    const controller = new AbortController();
    let version = 0;
    async function refresh() {
      const current = ++version;
      try {
        const response = await fetch(`/api/system/access/waits?session_id=${encodeURIComponent(sid)}`, {
          cache: "no-store", signal: controller.signal,
        });
        if (!response.ok) return;
        const data = await response.json() as { waits?: unknown[] };
        if (!controller.signal.aborted && current === version && Array.isArray(data.waits)) {
          const rows: AccessWait[] = data.waits
            .map((wait: unknown) => rememberSystemAccessWait(wait))
            .filter((wait): wait is AccessWait => wait !== null);
          // A successful, structurally valid response is authoritative for
          // this session. Cached live frames bridge a mount race only until
          // this response arrives; they must not survive an empty projection.
          const owned = rows.filter(wait => wait.session_id === sid);
          const activeIds = new Set(owned.map(wait => wait.wait_id));
          for (const waitId of live.current) {
            if (activeIds.has(waitId)) verified.current.add(waitId);
            else verified.current.delete(waitId);
          }
          for (const previous of rememberedSystemAccessWaits(sid)) {
            if (!activeIds.has(previous.wait_id)) {
              forgetSystemAccessWait(previous);
              live.current.delete(previous.wait_id);
              verified.current.delete(previous.wait_id);
            }
          }
          setWaits(owned);
        }
      } catch { /* Keep the last known wait; a failed read is not resolution. */ }
    }
    function update(event: Event) {
      const { type, data } = (event as CustomEvent).detail || {};
      if (data?.session_id !== sid) return;
      ++version;
      if (type === "system_access.waiting") {
        const wait = rememberSystemAccessWait(data, data.live === true);
        if (!wait) return;
        if (data.live === true && !systemAccessWaitHandled(wait)) {
          live.current.add(wait.wait_id);
          verified.current.delete(wait.wait_id);
        }
        setWaits(previous => [
          ...previous.filter(previousWait => previousWait.wait_id !== wait.wait_id),
          wait,
        ]);
      } else {
        forgetSystemAccessWait(data);
        live.current.delete(data.wait_id);
        verified.current.delete(data.wait_id);
        setWaits(previous => previous.filter(wait => wait.wait_id !== data.wait_id));
      }
      void refresh();
    }
    const reload = () => { void refresh(); };
    const executionChanged = (event: Event) => {
      if ((event as CustomEvent).detail?.execution?.session_id === sid) reload();
    };
    const connected = (event: Event) => { if ((event as CustomEvent).detail?.connected) reload(); };
    window.addEventListener("op:system-access", update);
    window.addEventListener("op:browser-connection", connected);
    window.addEventListener("focus", reload);
    window.addEventListener("op:execution-update", executionChanged);
    // Refresh presentation after a missed frame; execution recovery is in the worker.
    const timer = window.setInterval(() => { if (hasWaits.current) reload(); }, 2000);
    void refresh();
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("op:system-access", update);
      window.removeEventListener("op:browser-connection", connected);
      window.removeEventListener("focus", reload);
      window.removeEventListener("op:execution-update", executionChanged);
    };
  }, [sessionId]);
  if (!waits.length) return null;
  const required = [...new Set(waits.flatMap(wait => wait.required_capabilities))];
  return <div className="message system">
    <div className="message-content">
      <SystemAccessRecovery
        requiredCapabilities={required}
        autoOpen={waits.some(wait => !systemAccessWaitHandled(wait) && live.current.has(wait.wait_id)
          && verified.current.has(wait.wait_id))}
        onAutoOpen={() => {
          for (const wait of waits) {
            markSystemAccessWaitHandled(wait);
          }
        }}
      />
    </div>
  </div>;
}
