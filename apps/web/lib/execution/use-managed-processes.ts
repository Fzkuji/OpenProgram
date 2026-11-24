"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSessionStore } from "@/lib/session-store";
import "@/lib/net/ws/ws-events";
import { getProcess, getSessionProcesses, type AgentCall, type ManagedProcess, type ActiveToolCall } from "../net/execution/process-client";
import { activityReadKey, lastActivityRead, activityReadStart, rememberActivityRead } from "./activity-reads";
import { useChanged } from "../net/ws/changes";

/** One read of a conversation's programs and calls. */
type ProcessList = { sessionId: string; items: ManagedProcess[]; calls: ActiveToolCall[]; spawns: AgentCall[] };
const NONE: ProcessList = { sessionId: "", items: [], calls: [], spawns: [] };

/** One read of an open program: its record and the output it kept. */
type ProgramDetail = { process: ManagedProcess; output: string };

/** Reads persisted records; selection never consumes the process tool's log
 *  cursor. The conversation's last read, and an open program's last output,
 *  draw at once (activity-reads.ts). An open program is read alongside the
 *  list, not after it. The records are read again when the Worker says they
 *  changed (`processes`, written by each command's supervisor:
 *  host/processes/record_changes.py) or a run moved; nothing reads them on a
 *  timer. A read that fails keeps what is shown and is tried again a little
 *  later. */
export function useManagedProcesses(active: boolean, sessionId: string | null, selectedId: string | null) {
  const [read, setRead] = useState<ProcessList | null>(() => lastActivityRead<ProcessList>("processes", sessionId));
  const list = (read?.sessionId === sessionId ? read : lastActivityRead<ProcessList>("processes", sessionId)) ?? NONE;
  const { items, calls, spawns } = list;
  const loaded = list !== NONE;
  const programKey = sessionId && selectedId ? activityReadKey(sessionId, selectedId) : null;
  const [detailRead, setDetail] = useState<{ key: string; detail: ProgramDetail | null } | null>(null);
  const detail = programKey ? (detailRead?.key === programKey ? detailRead.detail : lastActivityRead<ProgramDetail>("program", programKey)) : null;
  const [failedSessionId, setFailedSessionId] = useState<string | null>(null);
  const stale = failedSessionId !== null && failedSessionId === sessionId;
  const refreshRef = useRef<() => Promise<boolean>>(() => Promise.resolve(false));
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => { setFailedSessionId(null); }, [sessionId]);
  // A supervisor wrote a program's output, progress or exit.
  useChanged("processes", () => refreshRef.current());
  useEffect(() => {
    if (!active || !sessionId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    let pending: Promise<boolean> | null = null;
    let dirty = false;
    let failures = 0;
    const poll = (): Promise<boolean> => {
      if (disposed || document.visibilityState === "hidden") return Promise.resolve(false);
      if (pending) { dirty = true; return pending; }
      dirty = false;
      clearTimeout(timer);
      pending = (async () => {
        controller = new AbortController();
        const startedAt = activityReadStart();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          // The open program is asked for together with the list: one round trip, not two.
          const program = selectedId ? getProcess(selectedId, controller.signal, sessionId) : null;
          program?.catch(() => undefined);
          const data = await getSessionProcesses(sessionId, controller.signal);
          if (disposed) return false;
          const read: ProcessList = { sessionId, items: data.items || [], calls: data.calls || [], spawns: data.spawns || [] };
          rememberActivityRead("processes", sessionId, read, startedAt);
          setRead(read);
          if (program && selectedId) {
            const key = activityReadKey(sessionId, selectedId);
            if (!read.items.some(item => item.id === selectedId)) {
              // The program has left this conversation; its page says so.
              rememberActivityRead<ProgramDetail | null>("program", key, null, startedAt);
              setDetail({ key, detail: null });
            } else {
              const selected = await program;
              if (!disposed && read.items.some(item => item.id === selected.process.id)) {
                rememberActivityRead("program", key, selected, startedAt);
                setDetail({ key, detail: selected });
              }
            }
          }
          if (disposed) return false;
          failures = 0;
          setFailedSessionId(null);
          return true;
        } catch (error) {
          if (disposed) return false;
          console.warn("Activity: could not read programs", error);
          failures += 1;
          setFailedSessionId(sessionId);
          return false;
        }
        finally {
          clearTimeout(timeout);
          pending = null;
          // A change that came during the read is read at once; only a read
          // that failed comes back on its own, a little later each time.
          if (!disposed && dirty) queueMicrotask(() => { void poll(); });
          else if (!disposed && failures) timer = setTimeout(poll, Math.min(30000, 1000 * 2 ** failures));
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
  return { items, calls, spawns, detail, loaded, stale, refresh };
}
