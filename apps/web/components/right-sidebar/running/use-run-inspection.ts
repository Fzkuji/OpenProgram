"use client";

/** The record of one open run: its waits, checkpoints, drafts and last 50 raw
 *  events. Read while the run is open and again when its version moves. */
import { useCallback, useEffect, useRef, useState } from "react";
import type { CheckpointInspector } from "../debugger/debugger-panel";
import type { DurableWait, EventCursor, ExecutionSnapshot, RevisionDraft } from "@/lib/execution/execution-debugger";
import { getExecutionDebuggerState, getExecutionEvents, inspectionFromDebuggerState, type PersistedExecutionEvent, type UnresolvedEffect } from "@/lib/net/execution/execution-client";

export interface RunInspection {
  events: PersistedExecutionEvent[];
  unresolvedEffects: UnresolvedEffect[];
  checkpoints: CheckpointInspector[];
  waits: DurableWait[];
  drafts: RevisionDraft[];
  cursor: EventCursor | null;
  refresh: () => Promise<void>;
}

type Loaded = Omit<RunInspection, "refresh">;
const EMPTY: Loaded = { events: [], unresolvedEffects: [], checkpoints: [], waits: [], drafts: [], cursor: null };

export function useRunInspection(snapshot: ExecutionSnapshot | null, sessionId: string, active: boolean): RunInspection {
  const [loaded, setLoaded] = useState<Loaded>(EMPTY);
  const executionId = snapshot?.execution_id ?? null;
  const sequence = snapshot?.event_sequence ?? 0;
  const version = snapshot?.status_version ?? 0;
  const alive = useRef(true);
  const reads = useRef<AbortController | null>(null);
  const reading = useRef(false);
  const dirty = useRef(false);
  const latest = useRef({ executionId, sequence });
  latest.current = { executionId, sequence };

  useEffect(() => {
    alive.current = true;
    reads.current = new AbortController();
    return () => { alive.current = false; reads.current?.abort(); };
  }, []);

  const refresh = useCallback(async () => {
    if (reading.current) { dirty.current = true; return; }
    reading.current = true;
    try {
      do {
        dirty.current = false;
        const { executionId: id, sequence: last } = latest.current;
        if (!id) return;
        try {
          const [replay, state] = await Promise.all([
            getExecutionEvents(id, Math.max(0, last - 50), reads.current?.signal, sessionId),
            getExecutionDebuggerState(id, reads.current?.signal, sessionId),
          ]);
          if (!alive.current || latest.current.executionId !== id) return;
          const { checkpoints, unresolvedEffects, waits, drafts } = inspectionFromDebuggerState(state);
          setLoaded({
            events: replay.events || [], cursor: replay.event_cursor || null, unresolvedEffects, waits, drafts,
            checkpoints: checkpoints as CheckpointInspector[],
          });
        } catch {
          // The last record stays on screen; the next version change reads again.
        }
      } while (dirty.current && alive.current);
    } finally {
      reading.current = false;
    }
  }, [sessionId]);

  useEffect(() => {
    if (active && executionId) void refresh();
  }, [active, executionId, sequence, version, refresh]);

  return { ...loaded, refresh };
}
