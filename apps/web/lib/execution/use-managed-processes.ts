"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSessionStore } from "@/lib/session-store";
import "@/lib/net/ws-events";
import { getProcess, getSessionProcesses, type ManagedProcess } from "../net/process-client";

/** Reads persisted records; selection never consumes the process tool's log cursor. */
export function useManagedProcesses(active: boolean, sessionId: string | null, selectedId: string | null) {
  const [items, setItems] = useState<ManagedProcess[]>([]);
  const [detail, setDetail] = useState<{ process: ManagedProcess; output: string } | null>(null);
  const [failedSessionId, setFailedSessionId] = useState<string | null>(null);
  const stale = failedSessionId !== null && failedSessionId === sessionId;
  const [loaded, setLoaded] = useState(false);
  const refreshRef = useRef<() => Promise<boolean>>(() => Promise.resolve(false));
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => { setItems([]); setDetail(null); setLoaded(false); setFailedSessionId(null); }, [sessionId]);
  useEffect(() => { setDetail(null); }, [sessionId, selectedId]);
  useEffect(() => {
    if (!active || !sessionId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    let pending: Promise<boolean> | null = null;
    let dirty = false;
    const poll = (): Promise<boolean> => {
      if (disposed || document.visibilityState === "hidden") return Promise.resolve(false);
      if (pending) { dirty = true; return pending; }
      dirty = false;
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
          setFailedSessionId(null);
          return true;
        } catch { if (!disposed) setFailedSessionId(sessionId); return false; }
        finally {
          clearTimeout(timeout);
          pending = null;
          if (!disposed) timer = setTimeout(poll, dirty ? 0 : 3000);
        }
      })();
      return pending;
    };
    refreshRef.current = poll;
    const onUpdate = () => { void poll(); };
    const unsubscribe = useSessionStore.subscribe((state, previous) => {
      if (state.wsStatus === "open" && previous.wsStatus !== "open") onUpdate();
    });
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    window.addEventListener("online", onUpdate);
    window.addEventListener("focus", onUpdate);
    document.addEventListener("visibilitychange", onUpdate);
    void poll();
    return () => {
      disposed = true;
      refreshRef.current = () => Promise.resolve(false);
      controller?.abort();
      clearTimeout(timer);
      unsubscribe();
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
      window.removeEventListener("online", onUpdate);
      window.removeEventListener("focus", onUpdate);
      document.removeEventListener("visibilitychange", onUpdate);
    };
  }, [active, sessionId, selectedId]);
  return { items, detail, loaded, stale, refresh };
}
