"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getProcess, getSessionProcesses, type ManagedProcess } from "./net/process-client";

/** Reads persisted records; selection never consumes the process tool's log cursor. */
export function useManagedProcesses(active: boolean, sessionId: string | null, selectedId: string | null) {
  const [items, setItems] = useState<ManagedProcess[]>([]);
  const [detail, setDetail] = useState<{ process: ManagedProcess; output: string } | null>(null);
  const [stale, setStale] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const refreshRef = useRef<() => Promise<boolean>>(() => Promise.resolve(false));
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => { setItems([]); setDetail(null); setLoaded(false); setStale(false); }, [sessionId]);
  useEffect(() => { setDetail(null); }, [sessionId, selectedId]);
  useEffect(() => {
    if (!active || !sessionId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    let pending: Promise<boolean> | null = null;
    const poll = (): Promise<boolean> => {
      if (pending) return pending;
      clearTimeout(timer);
      pending = (async () => {
        controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          const data = await getSessionProcesses(sessionId, controller.signal);
          if (disposed) return false;
          setItems(data.items); setLoaded(true);
          if (selectedId) {
            if (!data.items.some(item => item.id === selectedId)) {
              setDetail(null);
              throw new Error("Selected program is no longer in this conversation.");
            }
            const selected = await getProcess(selectedId, controller.signal, sessionId);
            if (!disposed && data.items.some(item => item.id === selected.process.id)) setDetail(selected);
          }
          if (disposed) return false;
          setStale(false);
          return true;
        } catch { if (!disposed) setStale(true); return false; }
        finally {
          clearTimeout(timeout);
          pending = null;
          if (!disposed) timer = setTimeout(poll, 3000);
        }
      })();
      return pending;
    };
    refreshRef.current = poll;
    void poll();
    return () => { disposed = true; refreshRef.current = () => Promise.resolve(false); controller?.abort(); clearTimeout(timer); };
  }, [active, sessionId, selectedId]);
  return { items, detail, loaded, stale, refresh };
}
