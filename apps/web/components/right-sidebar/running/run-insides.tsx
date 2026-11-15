"use client";

/** One run, opened in place: what happened in time order, what it waits on,
 *  what the reader can do, and Technical details. */
import { MessageSquareText } from "lucide-react";
import type { ActivityAttemptEvent, ActivityRun, ActivityRunEvent, ActivityRunEvents } from "@/lib/net/execution/activity-runs-client";
import type { ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import { processIsActive } from "@/lib/net/execution/process-client";
import type { ActivityMark, ActivityPanelContext, ActivityTone, Translate } from "./activity-page-types";
import { tickOf, type ActivityAxis } from "./activity-page-geometry";
import { ActivityActionRow, ActivityDetail, ActivityKeyValue, ActivityRow } from "./activity-page-row";
import { attemptLabel, clockTime, cleanRunTitle, eventLabel, stepTitle } from "./activity-run-wording";
import { ExecutionActionRows, ExecutionTechnicalRow, useExecutionControls } from "./execution-controls";
import { SubAgentRow } from "./sub-agent-row";
import { useRunInspection } from "./use-run-inspection";

export interface RunInsidesProps {
  rowId: string;
  run: ActivityRun;
  /** undefined until the first read answers. */
  events: ActivityRunEvents | undefined;
  failed: boolean;
  context: ActivityPanelContext;
  axis: ActivityAxis;
}

const inputText = (input: unknown) => typeof input === "string" ? input : input == null ? "" : Object.keys(input as object).length === 0 ? "" : JSON.stringify(input);

function eventKey(event: ActivityRunEvent, index: number): string {
  switch (event.kind) {
    case "asked": return "asked";
    case "replied": return "replied";
    case "step": return `step:${event.node_id}`;
    case "attempt": return `attempt:${event.number}`;
    case "subagent": return `agent:${event.job_id ?? index}`;
  }
}

const attemptFailed = (event: ActivityAttemptEvent, text: Translate) => attemptLabel(event, text).state === "failed";

/** What one event has to say past its line. */
export function EventDetail({ event, context }: { event: Exclude<ActivityRunEvent, { kind: "subagent" }>; context: ActivityPanelContext }) {
  const { text } = context;
  switch (event.kind) {
    case "asked": return <ActivityKeyValue label={event.by === "schedule" ? text("Asked by the schedule", "由定时任务提问") : event.by === "check-in" ? text("Check-in", "巡检") : text("Asked", "提问")} value={event.text} />;
    case "replied": return <ActivityKeyValue label={text("Reply", "回复")} value={cleanRunTitle(event.text, text)} />;
    case "step": return <>
      {inputText(event.input) ? <ActivityKeyValue label={text("Input", "输入")} value={inputText(event.input)} /> : null}
      <ActivityKeyValue label={text("Result", "结果")} value={event.result ?? ""} />
    </>;
    case "attempt": {
      const error = event.failure_text ? <ActivityKeyValue label={text("Error", "报错")} value={event.failure_text} /> : null;
      if (attemptFailed(event, text)) return error ?? (event.reason_code ? <ActivityKeyValue label={text("Reason code", "原因代码")} value={event.reason_code} /> : null);
      return <>
        {error}
        {event.outcome ? <ActivityKeyValue label={text("Outcome", "结果")} value={event.outcome} /> : null}
        {event.checkpoint_id ? <ActivityKeyValue label={text("Saved point", "保存点")} value={event.checkpoint_id} /> : null}
      </>;
    }
  }
}

function hasDetail(event: Exclude<ActivityRunEvent, { kind: "subagent" }>, text: Translate): boolean {
  if (event.kind !== "attempt") return true;
  return Boolean(event.failure_text || (attemptFailed(event, text) ? event.reason_code : event.outcome || event.checkpoint_id));
}

/** Where Create branch starts: the last saved attempt, else the run's end. */
function savedPointTime(run: ActivityRun, events: ActivityRunEvent[]): string | null {
  const attempts = events.filter((event): event is ActivityAttemptEvent => event.kind === "attempt" && Boolean(event.checkpoint_id));
  const newest = attempts.at(-1);
  const saved = attempts.find(event => event.checkpoint_id === newest?.checkpoint_id);
  if (saved) return clockTime(saved.at, true);
  return run.ended_at !== null ? clockTime(run.ended_at, true) : null;
}

export function RunInsides({ rowId, run, events, failed, context, axis }: RunInsidesProps) {
  const { text } = context;
  const start = run.started_at ?? run.ended_at ?? 0;
  const snapshot = run.execution_id ? context.controller.executions.find(item => item.execution_id === run.execution_id) ?? null : null;
  const wait = snapshot ? context.waitingFor(snapshot) : null;
  const list = events?.events ?? [];
  const tick = (at: number, tone: ActivityTone): ActivityMark => ({ kind: "tick", ...tickOf(at - start, axis), tone });
  const stepIds = new Set(list.flatMap(event => event.kind === "step" && event.tool_call_id ? [event.tool_call_id] : []));
  const agentOpen = list.some(event => event.kind === "subagent" && event.ended_at === null && !event.never_started);
  const liveCalls = run.execution_id ? (context.processes.calls ?? []).filter(call => call.execution_id === run.execution_id && call.status === "running"
    && !(call.tool_call_id && stepIds.has(call.tool_call_id)) && !(call.name === "agent" && agentOpen)) : [];
  const livePrograms = run.execution_id ? context.processes.items.filter(item => item.execution_id === run.execution_id && processIsActive(item) && item.status !== "unknown") : [];
  const running = text("Running", "正在运行");
  const attemptCount = list.filter(event => event.kind === "attempt").length;
  // One attempt that is going or went fine says nothing the run's own line does not.
  const routineAttempt = (event: ActivityRunEvent) => event.kind === "attempt" && attemptCount === 1 && ["running", "completed"].includes(attemptLabel(event, text).state);
  return <>
    {events === undefined ? <ActivityDetail><ActivityKeyValue label="" value={failed ? text("Could not read this run. Retrying automatically.", "读不到这次运行，正在自动重试。") : text("Loading…", "正在读取…")} /></ActivityDetail> : null}
    {list.map((event, index) => {
      if (routineAttempt(event)) return null;
      const key = `${rowId}:${eventKey(event, index)}`;
      if (event.kind === "subagent") return <SubAgentRow key={key} id={key} event={event} context={context} axis={axis} runStart={start} />;
      const { label, tone } = eventLabel(event, text);
      const failedMark = event.kind === "step" ? event.failed : event.kind === "attempt" && attemptLabel(event, text).state === "failed";
      return <ActivityRow key={key} id={key} status={label} tone={tone} time={clockTime(event.at, true)} mark={tick(event.at, failedMark ? "err" : "muted")}>
        {hasDetail(event, text) ? () => <ActivityDetail><EventDetail event={event} context={context} /></ActivityDetail> : undefined}
      </ActivityRow>;
    })}
    {liveCalls.map(call => <ActivityRow key={`${rowId}:call:${call.id}`} id={`${rowId}:call:${call.id}`} status={`${running} · ${call.description || stepTitle(call.name, undefined, text)}`} tone="run"
      time={clockTime(call.started_at, true)} mark={tick(call.started_at, "run")} />)}
    {livePrograms.map(item => <ActivityRow key={`${rowId}:program:${item.id}`} id={`${rowId}:program:${item.id}`} status={`${running} · ${item.title?.trim() || item.command.trim()}`} tone="run"
      time={clockTime(item.started_at, true)} mark={tick(item.started_at, "run")} />)}
    {wait && snapshot ? <RunWaiting rowId={rowId} snapshot={snapshot} wait={wait} context={context} now={tick(context.now, "warn")} /> : null}
    {snapshot ? <RunControls rowId={rowId} snapshot={snapshot} context={context} savedAt={savedPointTime(run, list)} /> : null}
  </>;
}

function RunWaiting({ rowId, snapshot, wait, context, now }: {
  rowId: string; snapshot: ExecutionSnapshot; wait: NonNullable<ReturnType<ActivityPanelContext["waitingFor"]>>; context: ActivityPanelContext; now: ActivityMark;
}) {
  const { text } = context;
  const question = wait.question || text("It paused on a question or an approval.", "它停在一个问题或授权上。");
  return <>
    <ActivityRow id={`${rowId}:waiting`} title={question} status={wait.waitLabel} tone="warn" time="" mark={now}>
      {wait.choices.length > 0 ? () => <ActivityDetail>
        <ActivityKeyValue label={text("Question", "问题")} value={question} />
        <ActivityKeyValue label={text("Choices", "可选")} value={wait.choices.join(text(" · ", "、"))} />
      </ActivityDetail> : undefined}
    </ActivityRow>
    {wait.answerLabel ? <ActivityActionRow icon={MessageSquareText} label={wait.answerLabel} note={text("Goes to its card in the chat", "转到聊天里的卡片")} onClick={() => context.answerInChat(snapshot)} /> : null}
  </>;
}

function RunControls({ rowId, snapshot, context, savedAt }: { rowId: string; snapshot: ExecutionSnapshot; context: ActivityPanelContext; savedAt: string | null }) {
  const inspection = useRunInspection(snapshot, context.sessionId, context.active);
  const controls = useExecutionControls(snapshot, inspection, context);
  return <>
    <ExecutionActionRows controls={controls} />
    <ExecutionTechnicalRow id={`${rowId}:technical`} controls={controls} branchFrom={savedAt} />
  </>;
}
