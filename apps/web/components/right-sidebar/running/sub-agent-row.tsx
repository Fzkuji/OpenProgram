"use client";

/** A sub-agent inside the run that started it: what it was asked, what it
 *  replied, what it started in turn, a way into its conversation, and the
 *  controls while it runs. */
import type { ReactNode } from "react";
import { MessageSquareText } from "lucide-react";
import type { ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import type { ActivitySubAgentEvent } from "@/lib/net/execution/activity-runs-client";
import { subAgentShape } from "@/lib/runtime-bridge/dag/render/agent-look";
import type { ActivityMark, ActivityPanelContext } from "./activity-page-types";
import { barOf, tickOf, type ActivityAxis } from "./activity-page-geometry";
import { ActivityActionRow, ActivityDetail, ActivityKeyValue, ActivityRow } from "./activity-page-row";
import { cleanRunTitle, clockTime, durationWords } from "./activity-run-wording";
import { ExecutionActionRows, ExecutionTechnicalRow, useExecutionControls } from "./execution-controls";
import { AgentGlyph } from "./running-rows";
import { useRunInspection } from "./use-run-inspection";

const LIVE = new Set(["queued", "running", "pausing", "cancelling"]);

export interface SubAgentRowProps {
  id: string;
  event: ActivitySubAgentEvent;
  context: ActivityPanelContext;
  axis: ActivityAxis;
  /** Seconds at which the parent run began. */
  runStart: number;
  /** Rows under it sit this much further in; multiples of 16. */
  indent?: number;
}

export function SubAgentRow({ id, event, context, axis, runStart, indent = 0 }: SubAgentRowProps) {
  const { text, now } = context;
  const snapshot = event.job_id ? context.controller.executions.find(item => item.execution_id === event.job_id) ?? null : null;
  const wait = snapshot ? context.waitingFor(snapshot) : null;
  const live = !event.never_started && event.ended_at === null && (snapshot ? LIVE.has(snapshot.status) : Boolean(event.status && LIVE.has(event.status)));
  const start = event.at - runStart;
  const span = event.ended_at !== null ? event.ended_at - event.at : null;
  const status = event.never_started ? { label: text("Failed · never started", "失败 · 没有启动"), tone: "err" as const }
    : wait ? { label: wait.waitLabel, tone: "warn" as const }
      : live ? { label: `${text("Running", "正在运行")} · ${durationWords(now - event.at, text)}`, tone: "run" as const }
        : event.failed ? { label: span !== null ? `${text("Failed", "失败")} · ${durationWords(span, text)}` : text("Failed", "失败"), tone: "err" as const }
          : { label: span !== null ? durationWords(span, text) : clockTime(event.at, true), tone: "muted" as const };
  const mark: ActivityMark = event.never_started
    ? { kind: "tick", ...tickOf(start, axis), tone: "err" }
    : { kind: "bar", ...barOf(start, event.ended_at !== null ? event.ended_at - runStart : null, axis), tone: status.tone };
  const inner = indent + 16;
  const name = event.name.replace(/_/g, " ");
  const replyLine = event.result ? cleanRunTitle(event.result, text) : "";
  const facts = <>
    {event.job_id ? <ActivityKeyValue label={text("Job", "任务")} value={event.job_id} /> : null}
    {event.agent_id ? <ActivityKeyValue label={text("Agent", "代理")} value={event.agent_id} /> : null}
    {event.context ? <ActivityKeyValue label={text("Context", "上下文")} value={event.context} /> : null}
    {event.closed_as ? <ActivityKeyValue label={text("Closed as", "结束方式")} value={event.closed_as} /> : null}
  </>;
  const hasFacts = Boolean(event.job_id || event.agent_id || event.context || event.closed_as);
  return <ActivityRow id={id} glyph={<AgentGlyph shape={subAgentShape(name)} />} title={name} status={status.label} tone={status.tone}
    indent={indent} time={clockTime(event.at, true)} mark={mark} defaultOpen={Boolean(wait)}>
    {() => <>
      {event.brief ? <ActivityRow id={`${id}:asked`} status={`${text("Asked", "提问")}: ${event.brief}`} indent={inner}>
        {() => <ActivityDetail indent={inner}><ActivityKeyValue label={text("Asked", "提问")} value={event.brief} /></ActivityDetail>}
      </ActivityRow> : null}
      {event.never_started || event.error ? <ActivityRow id={`${id}:error`} status={`${text("Failed", "失败")} · ${event.error ?? ""}`} tone="err" indent={inner}>
        {() => <ActivityDetail indent={inner}><ActivityKeyValue label={text("Error", "报错")} value={event.error ?? ""} /></ActivityDetail>}
      </ActivityRow> : null}
      {event.result ? <ActivityRow id={`${id}:replied`} status={`${text("Replied", "回复")}: ${replyLine.split("\n")[0]}`} indent={inner}>
        {() => <ActivityDetail indent={inner}><ActivityKeyValue label={text("Reply", "回复")} value={replyLine} /></ActivityDetail>}
      </ActivityRow> : null}
      {event.children.map((child, index) => <SubAgentRow key={child.job_id ?? index} id={`${id}:child:${child.job_id ?? index}`} event={child} context={context} axis={axis} runStart={runStart} indent={inner} />)}
      {wait ? <>
        {wait.question ? <ActivityRow id={`${id}:waiting`} title={wait.question} status={text("Waiting for your answer", "等你回答")} tone="warn" indent={inner} /> : null}
        {wait.answerLabel ? <ActivityActionRow icon={MessageSquareText} label={wait.answerLabel} note={text("Goes to its card in the chat", "转到聊天里的卡片")} indent={inner} onClick={() => context.answerInChat(snapshot!)} /> : null}
      </> : null}
      {event.conversation && event.job_id ? <ActivityActionRow icon={MessageSquareText} label={text("Open its conversation", "打开它的对话")} note={text("Replaces the chat in the middle", "替换中间的聊天")} indent={inner} onClick={() => context.openAgentConversation(event.job_id!)} /> : null}
      {snapshot
        ? <SubAgentControls id={id} snapshot={snapshot} context={context} indent={inner} facts={facts} />
        : hasFacts ? <ActivityRow id={`${id}:technical`} status={text("Technical details", "技术详情")} indent={inner}>{() => <ActivityDetail indent={inner}>{facts}</ActivityDetail>}</ActivityRow> : null}
    </>}
  </ActivityRow>;
}

function SubAgentControls({ id, snapshot, context, indent, facts }: { id: string; snapshot: ExecutionSnapshot; context: ActivityPanelContext; indent: number; facts: ReactNode }) {
  const inspection = useRunInspection(snapshot, context.sessionId, context.active);
  const controls = useExecutionControls(snapshot, inspection, context);
  return <>
    <ExecutionActionRows controls={controls} indent={indent} />
    <ExecutionTechnicalRow id={`${id}:technical`} controls={controls} branchFrom={null} indent={indent} extra={facts} />
  </>;
}
