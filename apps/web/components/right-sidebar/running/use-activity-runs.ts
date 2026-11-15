"use client";

/** The runs of one Activity page and the events of its open rows. Refreshes
 *  replace rows by id and keep how many are shown, so a poll never closes a
 *  row, moves the scroll or takes the focus. */
import { useCallback, useEffect, useRef, useState } from "react";
import { getActivityRunEvents, getActivityRuns, type ActivityRun, type ActivityRunEvent, type ActivityRunEvents, type ActivityRunPage } from "@/lib/net/execution/activity-runs-client";
import type { ActivityRunSource } from "./activity-page-types";
import { runRowId } from "./activity-run-wording";

const FIRST_PAGE = 20;
const NEXT_PAGE = 50;
const SERVER_LIMIT = 100;
const POLL_MS = 30000;
const CHART_FIRST_PAGE = 50;
const SORTING_POLL_MS = 4000;
const LIVE = new Set(["queued", "running", "pausing", "cancelling"]);

export { runRowId };

/** A poll covers only the newest rows. Everything older than the last row it
 *  also holds stays, so a run pushed off the polled page keeps its place. */
function mergeRunPage(previous: ActivityRun[], page: ActivityRunPage): ActivityRun[] {
  if (page.runs.length >= page.total) return page.runs;
  const fresh = new Set(page.runs.map(runRowId));
  let last = previous.length - 1;
  while (last >= 0 && !fresh.has(runRowId(previous[last]))) last -= 1;
  return last < 0 ? page.runs : [...page.runs, ...previous.slice(last + 1)];
}

/** A sub-agent can outlive its run, and the run's revision does not see it. */
const hasUnfinishedAgent = (events: ActivityRunEvent[]): boolean =>
  events.some(event => event.kind === "subagent" && !event.never_started && (event.ended_at === null || hasUnfinishedAgent(event.children)));

interface RunsState { runs: ActivityRun[]; total: number; loaded: boolean; failed: boolean; sorting: boolean }

export interface ActivityRunsOptions {
  sessionId: string;
  source: ActivityRunSource;
  active: boolean;
  isOpen: (rowId: string) => boolean;
  /** Changes whenever a row opens or closes. */
  openVersion: number;
  /** Every run comes with its steps and topic, so no row reads events. */
  chart?: boolean;
}

export function useActivityRuns({ sessionId, source, active, isOpen, openVersion, chart = false }: ActivityRunsOptions) {
  const firstPage = chart ? CHART_FIRST_PAGE : FIRST_PAGE;
  const sourceKey = source.kind === "task" ? `task:${source.taskId}` : "branch";
  const head = source.kind === "branch" ? source.head : null;
  const taskId = source.kind === "task" ? source.taskId : undefined;
  const [state, setState] = useState<RunsState>({ runs: [], total: 0, loaded: false, failed: false, sorting: false });
  const [generation, setGeneration] = useState(0);
  const [events, setEvents] = useState<Record<string, ActivityRunEvents>>({});
  const [eventsFailed, setEventsFailed] = useState<Record<string, boolean>>({});
  const count = useRef(firstPage);
  const reading = useRef(false);
  const dirty = useRef(false);
  const alive = useRef(true);
  const reads = useRef<AbortController | null>(null);
  const inFlight = useRef(new Set<string>());
  const query = useRef({ sessionId, head, taskId, chart });
  query.current = { sessionId, head, taskId, chart };
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    alive.current = true;
    reads.current = new AbortController();
    return () => { alive.current = false; reads.current?.abort(); };
  }, []);

  useEffect(() => {
    count.current = firstPage;
    setState({ runs: [], total: 0, loaded: false, failed: false, sorting: false });
    setEvents({});
    setEventsFailed({});
  }, [sessionId, sourceKey, firstPage]);

  const refresh = useCallback(async () => {
    if (reading.current) { dirty.current = true; return; }
    reading.current = true;
    try {
      do {
        dirty.current = false;
        const current = query.current;
        const limit = Math.min(count.current, SERVER_LIMIT);
        try {
          const page = await getActivityRuns(current.sessionId, current.taskId ? { taskId: current.taskId } : { head: current.head, offset: 0, limit, chart: current.chart }, reads.current?.signal);
          if (!alive.current || query.current.sessionId !== current.sessionId) return;
          setState(previous => {
            const runs = mergeRunPage(previous.runs, page);
            count.current = Math.max(count.current, runs.length);
            return { runs, total: page.total, loaded: true, failed: false, sorting: Boolean(page.topics_sorting) };
          });
          setGeneration(value => value + 1);
        } catch {
          if (alive.current) setState(previous => ({ ...previous, loaded: true, failed: true }));
        }
      } while (dirty.current && alive.current);
    } finally {
      reading.current = false;
    }
  }, []);

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
  }, [active, sessionId, sourceKey, head, refresh]);

  // Topics land a few seconds after a turn, or after opening an older chat.
  useEffect(() => {
    if (!active || !state.sorting) return;
    const timer = setTimeout(() => { void refresh(); }, SORTING_POLL_MS);
    return () => clearTimeout(timer);
  }, [active, state.sorting, generation, refresh]);

  // Events are read only for rows the reader has open.
  useEffect(() => {
    if (!active) return;
    for (const run of state.runs) {
      const key = runRowId(run);
      if (!isOpen(key) || inFlight.current.has(key)) continue;
      const cached = events[key];
      const live = run.execution_status !== null && LIVE.has(run.execution_status);
      if (cached && cached.revision === run.revision && !live && !hasUnfinishedAgent(cached.events)) continue;
      inFlight.current.add(key);
      getActivityRunEvents(sessionId, run.id, reads.current?.signal)
        .then(result => {
          if (!alive.current) return;
          setEvents(previous => ({ ...previous, [key]: result }));
          setEventsFailed(previous => previous[key] ? { ...previous, [key]: false } : previous);
        })
        .catch(() => { if (alive.current) setEventsFailed(previous => ({ ...previous, [key]: true })); })
        .finally(() => { inFlight.current.delete(key); });
    }
    // `events` is read for the revision only; a new result needs no new read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, generation, openVersion, sessionId]);

  /** `onFirst` hears the first new run before it is drawn. */
  const showMore = useCallback(async (onFirst?: (run: ActivityRun) => void): Promise<void> => {
    const { sessionId: session, head: currentHead, taskId: task, chart: withSteps } = query.current;
    if (task) return;
    const loaded = stateRef.current.runs.length;
    const page = await getActivityRuns(session, { head: currentHead, offset: loaded, limit: NEXT_PAGE, chart: withSteps }, reads.current?.signal);
    if (!alive.current) return;
    if (page.runs[0]) onFirst?.(page.runs[0]);
    count.current = loaded + page.runs.length;
    setState(previous => {
      const known = new Set(previous.runs.map(runRowId));
      return { ...previous, runs: [...previous.runs, ...page.runs.filter(run => !known.has(runRowId(run)))], total: page.total };
    });
  }, []);

  return { ...state, events, eventsFailed, showMore };
}
