"use client";

/** A scheduled task's runs for its page: the summary and the runs one filter
 *  keeps, a page at a time. Read on entry, when an execution or a Job moves,
 *  and when the Worker says the conversation's runs (`runs`) or the
 *  Scheduler's tasks and their runs (`work`) changed (lib/net/ws/changes.ts).
 *  A refresh replaces the newest rows by id and keeps how many are shown, so
 *  it never closes a row or moves the scroll. The last read of each filter
 *  draws at once when the page or the filter opens again (activity-reads.ts);
 *  a read that fails keeps what is shown and is tried again a little later,
 *  without a word. */
import { useCallback, useEffect, useRef, useState } from "react";
import { getTaskRuns, type TaskRun, type TaskRunFilter, type TaskRunPage } from "@/lib/net/execution/task-runs-client";
import { useChanged } from "@/lib/net/ws/changes";
import { activityReadKey, activityReadStart, lastActivityRead, rememberActivityRead } from "@/lib/execution/activity-reads";

export const TASK_FIRST_PAGE = 30;
export const TASK_NEXT_PAGE = 50;
const SERVER_LIMIT = 100;
/** A failed read is tried again after 2, 4, 8 … seconds, up to 30. */
const RETRY_MAX_MS = 30000;
const retryDelay = (failures: number) => Math.min(RETRY_MAX_MS, 1000 * 2 ** failures);

/** `loaded`: a read has answered, so an empty list really is empty.
 *  `failed`: the last read did not answer; what was read before stays. */
interface TaskRunsState { head: Omit<TaskRunPage, "page"> | null; runs: TaskRun[]; loaded: boolean; failed: boolean }
type TaskRunsRead = Pick<TaskRunsState, "head" | "runs">;

const keyOf = (sessionId: string, taskId: string, filter: TaskRunFilter) => activityReadKey(sessionId, taskId, filter);
const stateOf = (read: TaskRunsRead | null, head: TaskRunsState["head"] = null): TaskRunsState => read
  ? { head: read.head, runs: read.runs, loaded: true, failed: false }
  : { head, runs: [], loaded: false, failed: false };

/** The newest rows from a refresh, then the older ones it did not cover. */
export function mergeTaskRuns(previous: TaskRun[], fresh: TaskRun[], total: number): TaskRun[] {
  if (fresh.length >= total) return fresh;
  const seen = new Set(fresh.map(run => run.execution_id));
  let last = previous.length - 1;
  while (last >= 0 && !seen.has(previous[last].execution_id)) last -= 1;
  return last < 0 ? fresh : [...fresh, ...previous.slice(last + 1)];
}

export function useTaskRuns({ sessionId, taskId, filter, active }: { sessionId: string; taskId: string; filter: TaskRunFilter; active: boolean }) {
  const readKey = keyOf(sessionId, taskId, filter);
  const [state, setState] = useState<TaskRunsState>(() => stateOf(lastActivityRead<TaskRunsRead>("task-runs", readKey)));
  const shown = useRef(Math.max(TASK_FIRST_PAGE, state.runs.length));
  const alive = useRef(true);
  const reads = useRef<AbortController | null>(null);
  const failures = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const query = useRef({ sessionId, taskId, filter });
  query.current = { sessionId, taskId, filter };
  const shownKey = useRef(readKey);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      reads.current?.abort();
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, []);

  // Keep what is on screen for the next visit.
  useEffect(() => {
    if (state.loaded && shownKey.current === readKey) rememberActivityRead<TaskRunsRead>("task-runs", readKey, { head: state.head, runs: state.runs }, activityReadStart());
  }, [state, readKey]);

  const refresh = useCallback(async () => {
    const current = query.current;
    reads.current?.abort();
    if (retryTimer.current) { clearTimeout(retryTimer.current); retryTimer.current = null; }
    const controller = new AbortController();
    reads.current = controller;
    try {
      const { page, ...head } = await getTaskRuns(current.sessionId, current.taskId,
        { filter: current.filter, offset: 0, limit: Math.min(shown.current, SERVER_LIMIT) }, controller.signal);
      if (!alive.current || query.current.filter !== current.filter || query.current.taskId !== current.taskId) return;
      failures.current = 0;
      setState(previous => ({ head, runs: mergeTaskRuns(previous.runs, page, head.total), loaded: true, failed: false }));
    } catch (error) {
      if (!alive.current || controller.signal.aborted) return;
      console.warn("Activity: could not read a task's runs", error);
      setState(previous => previous.failed ? previous : { ...previous, failed: true });
      failures.current += 1;
      retryTimer.current = setTimeout(() => { retryTimer.current = null; void refresh(); }, retryDelay(failures.current));
    }
  }, []);

  // A new filter starts from its first page, or from what it showed last time.
  useEffect(() => {
    if (shownKey.current === readKey) return;
    shownKey.current = readKey;
    const read = lastActivityRead<TaskRunsRead>("task-runs", readKey);
    shown.current = Math.max(TASK_FIRST_PAGE, read?.runs.length ?? 0);
    failures.current = 0;
    setState(previous => stateOf(read, previous.head));
  }, [readKey]);

  useEffect(() => {
    if (!active) return;
    void refresh();
    const onUpdate = () => { void refresh(); };
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    return () => {
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
    };
  }, [active, readKey, refresh]);
  useChanged(["runs", "work"], () => (active ? refresh() : undefined), active ? [sessionId] : []);

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
