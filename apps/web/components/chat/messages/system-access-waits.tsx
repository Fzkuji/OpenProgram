"use client";

import { useEffect, useRef, useState } from "react";
import { SystemAccessRecovery } from "./system-access-recovery";

type AccessWait = {
  wait_id: string;
  session_id: string;
  execution_id: string;
  required_capabilities: string[];
};

/** This is a projection of worker-owned waits. It never dispatches a task. */
export function SystemAccessWaits({ sessionId }: { sessionId: string | null }) {
  const [waits, setWaits] = useState<AccessWait[]>([]);
  const live = useRef(new Set<string>());
  const hasWaits = useRef(false);
  hasWaits.current = waits.length > 0;
  useEffect(() => {
    setWaits([]);
    live.current.clear();
    if (!sessionId) return;
    const controller = new AbortController();
    let version = 0;
    async function refresh() {
      const current = ++version;
      try {
        const response = await fetch(`/api/system/access/waits?session_id=${encodeURIComponent(sessionId!)}`, {
          cache: "no-store", signal: controller.signal,
        });
        if (!response.ok) return;
        const data = await response.json();
        if (!controller.signal.aborted && current === version && Array.isArray(data.waits)) {
          setWaits(data.waits.filter((wait: AccessWait) => wait.session_id === sessionId));
        }
      } catch { /* Keep the last known wait; a failed read is not resolution. */ }
    }
    function update(event: Event) {
      const { type, data } = (event as CustomEvent).detail || {};
      if (data?.session_id !== sessionId) return;
      ++version;
      if (type === "system_access.waiting") {
        live.current.add(data.wait_id);
        setWaits(previous => [...previous.filter(wait => wait.wait_id !== data.wait_id), data]);
      } else {
        live.current.delete(data.wait_id);
        setWaits(previous => previous.filter(wait => wait.wait_id !== data.wait_id));
      }
      void refresh();
    }
    const reload = () => { void refresh(); };
    const executionChanged = (event: Event) => {
      if ((event as CustomEvent).detail?.execution?.session_id === sessionId) reload();
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
        autoOpen={waits.some(wait => live.current.has(wait.wait_id))}
        onAutoOpen={() => { for (const wait of waits) live.current.delete(wait.wait_id); }}
      />
    </div>
  </div>;
}
