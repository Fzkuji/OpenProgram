"use client";

/** A scheduled task's runs for its page: the summary and the runs one filter
 *  keeps, a page at a time. A refresh replaces the newest rows by id and keeps
 *  how many are shown, so it never closes a row or moves the scroll. */
import { useCallback, useEffect, useRef, useState } from "react";
import { getTaskRuns, type TaskRun, type TaskRunFilter, type TaskRunPage } from "@/lib/net/execution/task-runs-client";

export const TASK_FIRST_PAGE = 30;
export const TASK_NEXT_PAGE = 50;
const SERVER_LIMIT = 100;
const POLL_MS = 30000;

interface TaskRunsState { head: Omit<TaskRunPage, "page"> | null; runs: TaskRun[]; loaded: boolean; failed: boolean }

/** The newest rows from a refresh, then the older ones it did not cover. */
export function mergeTaskRuns(previous: TaskRun[], fresh: TaskRun[], total: number): TaskRun[] {
  if (fresh.length >= total) return fresh;
  const seen = new Set(fresh.map(run => run.execution_id));
  let last = previous.length - 1;
  while (last >= 0 && !seen.has(previous[last].execution_id)) last -= 1;
  return last < 0 ? fresh : [...fresh, ...previous.slice(last + 1)];
}

export function useTaskRuns({ sessionId, taskId, filter, active }: { sessionId: string; taskId: string; filter: TaskRunFilter; active: boolean }) {
  const [state, setState] = useState<TaskRunsState>({ head: null, runs: [], loaded: false, failed: false });
  const shown = useRef(TASK_FIRST_PAGE);
  const alive = useRef(true);
  const reads = useRef<AbortController | null>(null);
  const query = useRef({ sessionId, taskId, filter });
  query.current = { sessionId, taskId, filter };

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; reads.current?.abort(); };
  }, []);

  const refresh = useCallback(async () => {
    const current = query.current;
    reads.current?.abort();
    const controller = new AbortController();
    reads.current = controller;
    try {
      const { page, ...head } = await getTaskRuns(current.sessionId, current.taskId,
        { filter: current.filter, offset: 0, limit: Math.min(shown.current, SERVER_LIMIT) }, controller.signal);
      if (!alive.current || query.current.filter !== current.filter || query.current.taskId !== current.taskId) return;
      setState(previous => ({ head, runs: mergeTaskRuns(previous.runs, page, head.total), loaded: true, failed: false }));
    } catch {
      if (alive.current && !controller.signal.aborted) setState(previous => ({ ...previous, loaded: true, failed: true }));
    }
  }, []);

  // A new filter starts from its first page.
  useEffect(() => {
    shown.current = TASK_FIRST_PAGE;
    setState(previous => ({ ...previous, runs: [], loaded: false, failed: false }));
  }, [sessionId, taskId, filter]);

  useEffect(() => {
    if (!active) return;
    void refresh();
    const onUpdate = () => { void refresh(); };
    const timer = setInterval(onUpdate, POLL_MS);
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    return () => {
      clearInterval(timer);
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
    };
  }, [active, sessionId, taskId, filter, refresh]);

  const showEarlier = useCallback(async () => {
    const current = query.current;
    const loaded = state.runs.length;
    const { page, ...head } = await getTaskRuns(current.sessionId, current.taskId, { filter: current.filter, offset: loaded, limit: TASK_NEXT_PAGE });
    if (!alive.current || query.current.filter !== current.filter) return;
    shown.current = loaded + page.length;
    setState(previous => {
      const known = new Set(previous.runs.map(run => run.execution_id));
      return { ...previous, head, runs: [...previous.runs, ...page.filter(run => !known.has(run.execution_id))] };
    });
  }, [state.runs.length]);

  return { ...state, showEarlier };
}
