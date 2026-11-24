"use client";

/** The runs of every branch the conversation's chart draws, each with its
 *  steps. Read while the chart shows: on entry, when an execution or a Job
 *  moves, and when the Worker says the conversation's runs changed (`runs`:
 *  a Job's record, or topics sorted; lib/net/ws/changes.ts). Reads never
 *  overlap. Runs are held by branch, so a branch keeps what it had while it
 *  is read again, when the branches change order, and when a read fails. */
import { useEffect, useMemo, useRef, useState } from "react";
import { getActivityRuns, type ActivityRun } from "@/lib/net/execution/activity-runs-client";
import { useChanged } from "@/lib/net/ws/changes";

/** The newest runs of a branch the chart reads; its page of older ones is the Agent's own chart. */
const BRANCH_RUNS = 50;
const NO_RUNS: ActivityRun[] = [];

interface WorkRunsState { sessionId: string; runs: ReadonlyMap<string, ActivityRun[]> }

export function useWorkRuns(sessionId: string, heads: readonly (string | null)[], active: boolean): { runsByHead: ActivityRun[][]; loaded: boolean } {
  const key = heads.map(head => head ?? "").join("\n");
  // Which branches are read, in no order: a reorder reads nothing again.
  const wanted = [...new Set(heads.map(head => head ?? ""))].sort().join("\n");
  const [state, setState] = useState<WorkRunsState | null>(null);
  const query = useRef({ sessionId, heads });
  query.current = { sessionId, heads };
  const reader = useRef<(() => void) | null>(null);
  useChanged("runs", () => reader.current?.(), sessionId ? [sessionId] : []);

  useEffect(() => {
    if (!active || !sessionId) return;
    const abort = new AbortController();
    let alive = true, reading = false, dirty = false;
    const read = async () => {
      if (reading) { dirty = true; return; }
      reading = true;
      do {
        dirty = false;
        const asked = query.current;
        const pages = await Promise.all(asked.heads.map(head => getActivityRuns(asked.sessionId, { head, offset: 0, limit: BRANCH_RUNS, chart: true }, abort.signal).catch(() => null)));
        if (!alive) return;
        setState(previous => {
          const held = previous && previous.sessionId === asked.sessionId ? previous.runs : new Map<string, ActivityRun[]>();
          const runs = new Map<string, ActivityRun[]>();
          asked.heads.forEach((head, index) => {
            const got = pages[index]?.runs ?? held.get(head ?? "");
            if (got) runs.set(head ?? "", got);
          });
          return { sessionId: asked.sessionId, runs };
        });
      } while (dirty && alive);
      reading = false;
    };
    const onUpdate = () => { void read(); };
    void read();
    // Topics land a few seconds after a turn, or after opening an older
    // chat: sorting signals `runs` as it writes them and when it ends.
    reader.current = onUpdate;
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    return () => {
      alive = false; abort.abort();
      if (reader.current === onUpdate) reader.current = null;
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
    };
  }, [active, sessionId, wanted]);

  const held = state && state.sessionId === sessionId ? state.runs : null;
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the heads, in order
  const runsByHead = useMemo(() => heads.map(head => held?.get(head ?? "") ?? NO_RUNS), [held, key]);
  return { runsByHead, loaded: held !== null };
}
