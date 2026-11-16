"use client";

/** A scheduled task's page: how often it runs, how many times it ran and
 *  what that cost, a filter (All, Unfinished, Notified), then its runs by
 *  day, newest first, a page at a time. One line per run: a dot, when it
 *  began, how long it took and how it came out. A run that did not finish or
 *  could not check opens to one sentence of why and the helpers it started.
 *  Below the runs: Open in Scheduler and the task itself. */
import { useMemo, useState, type ReactNode } from "react";
import { Calendar } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtUsd } from "@/components/settings/providers/status/model";
import { navigate } from "@/lib/nav/navigate";
import type { ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import type { RosterStatus } from "@/lib/execution/agent-roster";
import type { TaskRun, TaskRunFilter } from "@/lib/net/execution/task-runs-client";
import type { ActivityPageProps, ActivityPanelContext } from "./activity-page-types";
import { activityGoneFrame, ActivityPageFrame } from "./activity-page";
import { ActivityActionRow, ActivityRow, ActivityShowMore } from "./activity-page-row";
import { clockTime, dayName } from "./activity-run-wording";
import { TaskDetails, taskLine } from "./scheduled-task-page";
import { RosterRow } from "../agent-roster";
import { StatusDot } from "../running-rows";
import { cronWords, duration, LIVE, taskOutcomeTone, taskOutcomeWords } from "../running-format";
import { TASK_NEXT_PAGE, useTaskRuns } from "../runs/use-task-runs";
import styles from "./task-runs-page.module.css";

const OPENS = new Set(["failed", "stopped", "interrupted", "unable"]);

/** "Yesterday · Nov 15", "Nov 10". */
function dayLabel(seconds: number, context: ActivityPanelContext): string {
  const name = dayName(seconds, context.text, context.now);
  const date = new Date(seconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  return name === date || !/^(Today|Yesterday|今天|昨天)$/.test(name) ? name : `${name} · ${date}`;
}

/** The helpers a run started, from the conversation's executions. */
function helpersOf(executions: ExecutionSnapshot[]): (runId: string) => ExecutionSnapshot[] {
  const byId = new Map(executions.map(item => [item.execution_id, item]));
  const rootOf = (item: ExecutionSnapshot) => {
    let top = item;
    for (const seen = new Set<string>(); ;) {
      const parent = top.view_parent_execution_id ?? top.parent_execution_id;
      const next = parent ? byId.get(parent) : undefined;
      if (!next || seen.has(next.execution_id)) return top.execution_id;
      seen.add(next.execution_id);
      top = next;
    }
  };
  const helpers = new Map<string, ExecutionSnapshot[]>();
  for (const item of executions) {
    if (item.display?.kind !== "job_agent") continue;
    const root = rootOf(item);
    if (root !== item.execution_id) helpers.set(root, [...(helpers.get(root) ?? []), item]);
  }
  return runId => helpers.get(runId) ?? [];
}

function helperStatus(item: ExecutionSnapshot): RosterStatus {
  if (item.status === "paused") return "input";
  if (LIVE.includes(item.status)) return "running";
  if (item.status === "failed") return "failed";
  return "idle";
}

export function TaskRunsPage({ target, context }: ActivityPageProps<"task">) {
  const { text, work, now } = context;
  const [filter, setFilter] = useState<TaskRunFilter>("all");
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  const task = work?.routines.find(entry => entry.id === target.taskId);
  const { head, runs, loaded, failed, showEarlier } = useTaskRuns({ sessionId: context.sessionId, taskId: target.taskId, filter, active: context.active });
  const helpers = useMemo(() => helpersOf(context.controller.executions), [context.controller.executions]);
  if (!task) return work ? activityGoneFrame(text) : <ActivityPageFrame kind={null} title={null} backLabel={text("Scheduled task", "定时任务")} axis={null}>{null}</ActivityPageFrame>;

  const summary = head ?? task.summary ?? null;
  const total = summary?.runs ?? 0;
  const cost = head?.cost ?? task.summary?.cost ?? null;
  const toggle = (id: string) => setOpened(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const rows: ReactNode[] = [];
  let previousDay: string | null = null;
  for (const run of runs) {
    const day = dayLabel(run.started_at, context);
    if (day !== previousDay) {
      previousDay = day;
      rows.push(<div key={`day:${day}`} className={styles.day}>{day}</div>);
    }
    const tone = taskOutcomeTone(run.outcome);
    const openable = OPENS.has(run.outcome);
    const open = openable && opened.has(run.execution_id);
    const took = run.ended_at !== null ? run.ended_at - run.started_at : run.outcome === "running" ? now - run.started_at : null;
    const line = <>
      <StatusDot tone={tone} />
      <span className={styles.tm}>{clockTime(run.started_at)}</span>
      <span className={styles.du}>{took !== null ? duration(took, text) : ""}</span>
      <span className={styles.rs} data-tone={tone}>{taskOutcomeWords(run.outcome, text)}</span>
    </>;
    rows.push(openable
      ? <Button key={run.execution_id} type="button" variant="ghost" className={styles.run} data-row-id={run.execution_id} tabIndex={-1}
        aria-expanded={open} data-open={open ? "" : undefined} onClick={() => toggle(run.execution_id)}>{line}</Button>
      : <div key={run.execution_id} className={styles.run} data-row-id={run.execution_id} tabIndex={-1}>{line}</div>);
    if (open) rows.push(<RunInside key={`in:${run.execution_id}`} run={run} helpers={helpers(run.execution_id)} context={context} />);
  }
  const earlier = Math.min(TASK_NEXT_PAGE, (head?.total ?? 0) - runs.length);
  const unfinished = summary?.unfinished ?? 0;
  // "Every hour · 178 runs · $20.89"
  const facts: ReactNode[] = [];
  if (task.cron) facts.push(<span key="when">{cronWords(task.cron, text)}</span>);
  facts.push(<b key="runs">{text(`${total} run${total === 1 ? "" : "s"}`, `${total} 次运行`)}</b>);
  if (cost !== null) facts.push(<span key="cost">{fmtUsd(cost)}</span>);

  return <ActivityPageFrame kind={null} title={null} backLabel={task.title} axis={null}>
    <div className={styles.kv}>{facts.flatMap((part, index) => index ? [<span key={`sep${index}`} aria-hidden="true">·</span>, part] : [part])}</div>
    <Tabs value={filter} onValueChange={value => setFilter(value as TaskRunFilter)} className="mx-2 mt-1.5 mb-1 gap-0">
      <TabsList aria-label={text("Which runs", "显示哪些运行")} className={styles.filterList}>
        <TabsTrigger value="all" className={styles.filterTab}>{text("All", "全部")}</TabsTrigger>
        <TabsTrigger value="unfinished" className={styles.filterTab}>{unfinished ? text(`Unfinished · ${unfinished}`, `没完成 · ${unfinished}`) : text("Unfinished", "没完成")}</TabsTrigger>
        <TabsTrigger value="notified" className={styles.filterTab}>{text("Notified", "已通知")}</TabsTrigger>
      </TabsList>
    </Tabs>
    {rows}
    {loaded && runs.length === 0 ? <p className={styles.empty}>{failed
      ? text("Could not read the runs. Retrying automatically.", "读不到运行记录，正在自动重试。")
      : filter === "all" ? text("No runs yet.", "还没有运行。") : filter === "unfinished" ? text("Every run finished.", "每次运行都完成了。") : text("No run has told you anything yet.", "还没有哪次运行通知过你。")}</p> : null}
    {earlier > 0 ? <ActivityShowMore label={text(`Show ${earlier} earlier`, `显示更早的 ${earlier} 次`)} onClick={() => void showEarlier().catch(() => {})} /> : null}
    <div className="mt-3">
      <ActivityActionRow icon={Calendar} label={text("Open in Scheduler", "在定时任务中打开")} note={text("Change or pause this task", "修改或暂停这个任务")} onClick={() => navigate("/scheduler")} />
      <ActivityRow id="task-details" status={taskLine(task, context)}>{() => <TaskDetails task={task} context={context} />}</ActivityRow>
    </div>
  </ActivityPageFrame>;
}

/** An opened run: why it did not finish, then the helpers it started. */
function RunInside({ run, helpers, context }: { run: TaskRun; helpers: ExecutionSnapshot[]; context: ActivityPanelContext }) {
  const { text } = context;
  return <div className={styles.inside}>
    {run.reason ? <p className={styles.note}>{text(run.reason.en, run.reason.zh)}</p> : null}
    {helpers.map(item => <RosterRow key={item.execution_id} name={(item.display?.label || item.task_label || text("Sub-agent", "子代理")).replace(/_/g, " ")}
      status={helperStatus(item)} indent={14} text={text} onOpen={() => context.openAgentConversation(item.execution_id)} />)}
  </div>;
}
