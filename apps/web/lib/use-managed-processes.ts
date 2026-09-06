"use client";

import { useCallback, useEffect, useState } from "react";
import { getProcess, getSessionProcesses, type ManagedProcess } from "./net/process-client";

/** Reads persisted records; selection never consumes the process tool's log cursor. */
export function useManagedProcesses(active: boolean, sessionId: string | null, selectedId: string | null) {
  const [items, setItems] = useState<ManagedProcess[]>([]);
  const [detail, setDetail] = useState<{ process: ManagedProcess; output: string } | null>(null);
  const [stale, setStale] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion(v => v + 1), []);
  useEffect(() => { setItems([]); setDetail(null); setLoaded(false); setStale(false); }, [sessionId]);
  useEffect(() => { setDetail(null); }, [sessionId, selectedId]);
  useEffect(() => {
    if (!active || !sessionId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const poll = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const data = await getSessionProcesses(sessionId, controller.signal);
        if (disposed) return;
        setItems(data.items); setLoaded(true);
        if (selectedId) {
          if (!data.items.some(item => item.id === selectedId)) {
            setDetail(null);
            throw new Error("Selected program is no longer in this conversation.");
          }
          const selected = await getProcess(selectedId, controller.signal, sessionId);
          if (!disposed && data.items.some(item => item.id === selected.process.id)) setDetail(selected);
        }
        if (!disposed) setStale(false);
      } catch { if (!disposed) setStale(true); }
      finally {
        clearTimeout(timeout);
        if (!disposed) timer = setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => { disposed = true; controller?.abort(); clearTimeout(timer); };
  }, [active, sessionId, selectedId, version]);
  return { items, detail, loaded, stale, refresh };
}
