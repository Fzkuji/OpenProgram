"use client";

/** The runs of one Activity page and the events of its open rows. Read on
 *  entry, when an execution or a Job moves, and when the Worker says the
 *  conversation's runs changed (`runs`: a Job's record, topics sorted) or,
 *  for a task's page, its runs (`work`; lib/net/ws/changes.ts). Refreshes
 *  replace rows by id and keep how many are shown, so a refresh never closes
 *  a row, moves the scroll or takes the focus.
 *
 *  What a page read is kept for the life of the window (activity-reads.ts):
 *  opening the page again, or another conversation and back, draws the last
 *  runs and the events of their open rows at once and reads again behind
 *  them. A read that fails leaves the last one on screen and is tried again
 *  a little later, without a word. */
import { useCallback, useEffect, useRef, useState } from "react";
import { getActivityRunEvents, getActivityRuns, type ActivityRun, type ActivityRunEvent, type ActivityRunEvents, type ActivityRunPage } from "@/lib/net/execution/activity-runs-client";
import { activityReadIsFresh, activityReadKey, activityReadStart, lastActivityRead, rememberActivityRead } from "@/lib/execution/activity-reads";
import type { ActivityRunSource } from "../pages/activity-page-types";
import { runRowId } from "../pages/activity-run-wording";
import { useChanged } from "@/lib/net/ws/changes";

const FIRST_PAGE = 20;
const NEXT_PAGE = 50;
const SERVER_LIMIT = 100;
const CHART_FIRST_PAGE = 50;
const LIVE = new Set(["queued", "running", "pausing", "cancelling"]);
/** A failed read is tried again after 2, 4, 8 … seconds, up to 30. */
const RETRY_MAX_MS = 30000;
const retryDelay = (failures: number) => Math.min(RETRY_MAX_MS, 1000 * 2 ** failures);

export { runRowId };

/** A refresh covers only the newest rows. Everything older than the last row
 *  it also holds stays, so a run pushed off the refreshed page keeps its place. */
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

/** `loaded`: a read has answered, so an empty list really is empty.
 *  `failed`: the last read did not answer; what was read before stays. */
interface RunsState { runs: ActivityRun[]; total: number; loaded: boolean; failed: boolean; sorting: boolean }

/** What one page keeps between visits. */
interface RunsRead { runs: ActivityRun[]; total: number; sorting: boolean; events: Record<string, ActivityRunEvents> }

const sourceKeyOf = (source: ActivityRunSource) => source.kind === "task" ? `task:${source.taskId}` : `branch:${source.head ?? ""}`;
const readKeyOf = (sessionId: string, sourceKey: string, chart: boolean) => activityReadKey(sessionId, sourceKey, chart ? "chart" : "list");
const stateOf = (read: RunsRead | null): RunsState => read
  ? { runs: read.runs, total: read.total, loaded: true, failed: false, sorting: read.sorting }
  : { runs: [], total: 0, loaded: false, failed: false, sorting: false };

const prefetching = new Set<string>();

/** Read a page's first runs before it opens (the reader is pointing at its
 *  row), so the page draws them at once. Nothing happens when they were
 *  already read. */
export function prefetchActivityRuns(sessionId: string, source: ActivityRunSource, chart = false): void {
  const key = readKeyOf(sessionId, sourceKeyOf(source), chart);
  if (prefetching.has(key) || lastActivityRead<RunsRead>("runs", key)) return;
  prefetching.add(key);
  const startedAt = activityReadStart();
  const query = source.kind === "task" ? { taskId: source.taskId } : { head: source.head, offset: 0, limit: chart ? CHART_FIRST_PAGE : FIRST_PAGE, chart };
  getActivityRuns(sessionId, query)
    .then(page => {
      if (lastActivityRead<RunsRead>("runs", key)) return;
      rememberActivityRead<RunsRead>("runs", key, { runs: page.runs, total: page.total, sorting: Boolean(page.topics_sorting), events: {} }, startedAt);
    })
    .catch(error => { console.warn("Activity: could not read runs ahead", error); })
    .finally(() => { prefetching.delete(key); });
}

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
  const sourceKey = sourceKeyOf(source);
  const head = source.kind === "branch" ? source.head : null;
  const taskId = source.kind === "task" ? source.taskId : undefined;
  const readKey = readKeyOf(sessionId, sourceKey, chart);
  const [state, setState] = useState<RunsState>(() => stateOf(lastActivityRead<RunsRead>("runs", readKey)));
  const [generation, setGeneration] = useState(0);
  const [events, setEvents] = useState<Record<string, ActivityRunEvents>>(() => lastActivityRead<RunsRead>("runs", readKey)?.events ?? {});
  // Bumped when a row's events could not be read, so they are read again.
  const [eventsRetry, setEventsRetry] = useState(0);
  const count = useRef(Math.max(firstPage, state.runs.length));
  const reading = useRef(false);
  const dirty = useRef(false);
  const alive = useRef(true);
  const failures = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const eventFailures = useRef(0);
  const reads = useRef<AbortController | null>(null);
  const inFlight = useRef(new Set<string>());
  const query = useRef({ sessionId, head, taskId, chart, readKey });
  query.current = { sessionId, head, taskId, chart, readKey };
  const stateRef = useRef(state);
  stateRef.current = state;
  const shownKey = useRef(readKey);
  // When the runs on screen were asked for; null while they are a kept read.
  const readAt = useRef<{ at: number; moves: number } | null>(null);

  useEffect(() => {
    alive.current = true;
    reads.current = new AbortController();
    return () => {
      alive.current = false;
      reads.current?.abort();
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, []);

  // Another conversation or page draws what was last read of it.
  useEffect(() => {
    if (shownKey.current === readKey) return;
    shownKey.current = readKey;
    const read = lastActivityRead<RunsRead>("runs", readKey);
    count.current = Math.max(firstPage, read?.runs.length ?? 0);
    failures.current = 0;
    readAt.current = null;
    setState(stateOf(read));
    setEvents(read?.events ?? {});
  }, [readKey, firstPage]);

  // Keep what is on screen for the next visit. Rows drawn from a kept read
  // keep counting as old, so the next visit reads again.
  useEffect(() => {
    if (!state.loaded || shownKey.current !== readKey) return;
    rememberActivityRead<RunsRead>("runs", readKey, { runs: state.runs, total: state.total, sorting: state.sorting, events }, readAt.current ?? { at: 0, moves: -1 });
  }, [state, events, readKey]);

  const refresh = useCallback(async () => {
    if (reading.current) { dirty.current = true; return; }
    reading.current = true;
    if (retryTimer.current) { clearTimeout(retryTimer.current); retryTimer.current = null; }
    try {
      do {
        dirty.current = false;
        const current = query.current;
        const limit = Math.min(count.current, SERVER_LIMIT);
        const startedAt = activityReadStart();
        try {
          const page = await getActivityRuns(current.sessionId, current.taskId ? { taskId: current.taskId } : { head: current.head, offset: 0, limit, chart: current.chart }, reads.current?.signal);
          if (!alive.current || query.current.readKey !== current.readKey) return;
          failures.current = 0;
          readAt.current = startedAt;
          setState(previous => {
            const runs = mergeRunPage(previous.runs, page);
            count.current = Math.max(count.current, runs.length);
            return { runs, total: page.total, loaded: true, failed: false, sorting: Boolean(page.topics_sorting) };
          });
          setGeneration(value => value + 1);
        } catch (error) {
          if (!alive.current || reads.current?.signal.aborted) return;
          console.warn("Activity: could not read runs", error);
          setState(previous => previous.failed ? previous : { ...previous, failed: true });
          failures.current += 1;
          retryTimer.current = setTimeout(() => { retryTimer.current = null; void refresh(); }, retryDelay(failures.current));
          return;
        }
      } while (dirty.current && alive.current);
    } finally {
      reading.current = false;
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    // Runs read a moment ago, with nothing moved since, are what the server would say.
    if (!activityReadIsFresh("runs", readKey)) void refresh();
    const onUpdate = () => { void refresh(); };
    window.addEventListener("op:execution-update", onUpdate);
    window.addEventListener("op:job-status", onUpdate);
    return () => {
      window.removeEventListener("op:execution-update", onUpdate);
      window.removeEventListener("op:job-status", onUpdate);
    };
  }, [active, readKey, refresh]);

  // Topics land a few seconds after a turn, or after opening an older chat:
  // sorting signals `runs` as it writes them and when it ends.
  useChanged(taskId ? ["runs", "work"] : "runs", () => (active ? refresh() : undefined), active && sessionId ? [sessionId] : []);

  // Events are read only for rows the reader has open.
  useEffect(() => {
    if (!active) return;
    let retry: ReturnType<typeof setTimeout> | null = null;
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
          eventFailures.current = 0;
          setEvents(previous => ({ ...previous, [key]: result }));
        })
        .catch(error => {
          if (!alive.current || reads.current?.signal.aborted) return;
          console.warn("Activity: could not read a run's events", error);
          eventFailures.current += 1;
          if (!retry) retry = setTimeout(() => setEventsRetry(value => value + 1), retryDelay(eventFailures.current));
        })
        .finally(() => { inFlight.current.delete(key); });
    }
    return () => { if (retry) clearTimeout(retry); };
    // `events` is read for the revision only; a new result needs no new read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, generation, openVersion, sessionId, eventsRetry]);

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

  return { ...state, events, showMore };
}
