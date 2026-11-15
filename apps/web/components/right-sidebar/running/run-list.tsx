"use client";

/** The runs of a branch or task, newest first under day labels, each opening
 *  in place. The axis covers the longest finished run shown and what happened
 *  in the open ones. */
import { useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore } from "react";
import type { ActivityRun } from "@/lib/net/execution/activity-runs-client";
import type { ActivityMark, RunListProps } from "./activity-page-types";
import { axisFor, barOf, tickOf, type ActivityAxis } from "./activity-page-geometry";
import { ActivityDayRow, ActivityDetail, ActivityInListReact, ActivityKeyValue, ActivityRow, ActivityShowMore, useActivityRowStore } from "./activity-page-row";
import { cleanRunTitle, clockTime, dayName, runOutcome, runStatusLine, runTitle } from "./activity-run-wording";
import { RunInsides } from "./run-insides";
import { runRowId, useActivityRuns } from "./use-activity-runs";

const AUTO_OPEN = new Set(["failed", "check-failed", "waiting", "running"]);

export function RunList({ source, context, before, renderInside, onAxis, reportName }: RunListProps) {
  const { text, now } = context;
  const store = useActivityRowStore();
  const openVersion = useSyncExternalStore(store.subscribe, store.version, store.version);
  const { runs, total, loaded, failed, events, eventsFailed, showMore } = useActivityRuns({
    sessionId: context.sessionId, source, active: context.active, isOpen: store.has, openVersion,
  });

  const executionOf = (run: ActivityRun) => run.execution_id ? context.controller.executions.find(item => item.execution_id === run.execution_id) ?? null : null;
  const waitOf = (run: ActivityRun) => { const execution = executionOf(run); return execution ? context.waitingFor(execution) : null; };

  // Decided once per page, on the first runs it sees; a close after that sticks.
  const decidedAutoOpen = useRef(false);
  if (!decidedAutoOpen.current && runs.length > 0) {
    decidedAutoOpen.current = true;
    const newest = runs[0];
    if (!newest.is_report && AUTO_OPEN.has(runOutcome(newest, Boolean(waitOf(newest))))) store.seed(runRowId(newest));
  }

  const axis = useMemo<ActivityAxis>(() => {
    const finished: number[] = [];
    const offsets: number[] = [];
    for (const run of runs) {
      if (!run.is_report && run.started_at !== null && run.ended_at !== null && runOutcome(run, false) !== "interrupted") finished.push(run.ended_at - run.started_at);
      const key = runRowId(run);
      if (run.started_at === null || !store.has(key)) continue;
      for (const event of events[key]?.events ?? []) {
        offsets.push(event.at - run.started_at);
        if ("ended_at" in event && event.ended_at !== null) offsets.push(event.ended_at - run.started_at);
      }
    }
    return axisFor(finished, offsets);
    // The open set is read through `store`; `openVersion` says when it moved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs, events, openVersion, store]);

  useEffect(() => { onAxis?.(axis); }, [axis.seconds, axis.divisions]); // eslint-disable-line react-hooks/exhaustive-deps

  const focusNext = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (!focusNext.current) return;
    const rowId = focusNext.current;
    focusNext.current = null;
    document.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(rowId)}"]`)?.focus({ preventScroll: true });
  }, [runs]);
  const loadEarlier = () => showMore(first => { focusNext.current = runRowId(first); }).catch(() => {});

  let previousDay: string | null = null;
  const dayCounts = new Map<string, number>();
  const rows = runs.flatMap(run => {
    const rowId = runRowId(run);
    const start = run.started_at ?? run.ended_at;
    const wait = waitOf(run);
    const outcome = runOutcome(run, Boolean(wait));
    const call = outcome === "running" && run.execution_id ? (context.processes.calls ?? []).filter(item => item.execution_id === run.execution_id && item.status === "running").at(-1) : undefined;
    const doing = call ? (call.name === "agent" ? text("Waiting for a sub-agent", "等待子代理") : `${text("Running", "正在运行")} ${call.description || call.name}`) : undefined;
    const look = runStatusLine(run, outcome, { now, text, waitingLabel: wait?.waitLabel, doing });
    const mark: ActivityMark = run.is_report || run.started_at === null
      ? { kind: "tick", ...tickOf(0, axis), tone: "muted" }
      : { kind: "bar", ...barOf(0, run.ended_at !== null && outcome !== "interrupted" ? run.ended_at - run.started_at : null, axis), tone: look.markTone };
    const openable = run.event_count > 0 || Boolean(run.asked_preview.trim() || run.reply.trim());
    const inside = () => {
      const custom = renderInside?.(run);
      if (custom) return custom;
      if (run.is_report) return <ActivityDetail><ActivityKeyValue label={text("Report", "报告")} value={cleanRunTitle(run.reply, text)} /></ActivityDetail>;
      return <RunInsides rowId={rowId} run={run} events={events[rowId]} failed={Boolean(eventsFailed[rowId])} context={context} axis={axis} />;
    };
    const day = start !== null ? dayName(start, text, now) : previousDay;
    let dayRow = null;
    if (day !== null && day !== previousDay) {
      previousDay = day;
      const seen = dayCounts.get(day) ?? 0;
      dayCounts.set(day, seen + 1);
      dayRow = <ActivityDayRow key={`day:${day}:${seen}`} name={day} axis={axis} />;
    }
    return [dayRow, <ActivityRow key={rowId} id={rowId} title={runTitle(run, text, reportName?.(run))} status={look.label} tone={look.tone} time={start !== null ? clockTime(start) : ""} mark={mark}>{openable ? inside : undefined}</ActivityRow>];
  });
  const earlier = Math.min(50, total - runs.length);

  return <>
    {before}
    <ActivityInListReact.Provider value={true}>
      {rows}
      {runs.length === 0 && loaded ? <ActivityDetail><ActivityKeyValue label="" value={failed ? text("Could not read the runs. Retrying automatically.", "读不到运行记录，正在自动重试。") : text("No runs yet.", "还没有运行。")} /></ActivityDetail> : null}
      {earlier > 0 ? <ActivityShowMore label={text(`Show ${earlier} earlier`, `显示更早的 ${earlier} 个`)} onClick={() => void loadEarlier()} /> : null}
    </ActivityInListReact.Provider>
  </>;
}
