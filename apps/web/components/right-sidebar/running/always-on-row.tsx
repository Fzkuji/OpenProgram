"use client";

/** The conversation's always-on setting, as one row above an agent's runs,
 *  and the insides of a report among those runs. */
import { EyeOff, Play, Trash2 } from "lucide-react";
import { AgentAvatarById } from "@/components/agents/agent-avatar";
import { aliveLabel, deliveryLabel, goalStatusLabel, outcomeLabel, reportKindTag, scheduleLabel, spendLabel, wakeCostLabel } from "@/components/agents/always-on-labels";
import { addReportPreference, dontReportText, removeReportPreference, wakeConversation, type ConversationReport } from "@/lib/chat/conversation-work";
import type { ActivityRun } from "@/lib/net/execution/activity-runs-client";
import { updatedTime } from "../debugger/debugger-presentation";
import type { ActivityPanelContext, AlwaysOnRowProps, ReportInsideProps } from "./activity-page-types";
import { ActivityActionRow, ActivityDetail, ActivityKeyValue, ActivityNote, ActivityRow } from "./activity-page-row";
import { Glyph } from "./running-rows";
import { duration, when } from "./running-format";
import styles from "./activity-page.module.css";

const reportsOf = (context: ActivityPanelContext) => context.work?.always_on.reports ?? [];
/** The message a report is posted as (always_on/store.py, report_message_id). */
const messageIdOf = (report: ConversationReport) => `report_${report.id.slice(0, 20)}`;
const reportOf = (run: ActivityRun, context: ActivityPanelContext) => run.is_report ? reportsOf(context).find(report => messageIdOf(report) === run.id) ?? null : null;

/** A report the always-on record still holds; an older one shows its message. */
export function reportForRun(run: ActivityRun, context: ActivityPanelContext): boolean {
  return reportOf(run, context) !== null;
}

/** What a report with no saved text goes by: its record's title, else the
 *  conversation's scheduled task when it has just one. */
export function reportNameForRun(run: ActivityRun, context: ActivityPanelContext): string | null {
  const title = reportOf(run, context)?.title.trim();
  if (title) return title;
  const routines = context.work?.routines ?? [];
  return routines.length === 1 ? routines[0].title : null;
}

function ReportBody({ report, context, indent }: { report: ConversationReport; context: ActivityPanelContext; indent?: number }) {
  const { text, sessionId, pendingKey } = context;
  const preferences = context.work?.always_on.preferences ?? [];
  const key = `dont:${report.id}`;
  const told = preferences.some(item => item.text === dontReportText(report.title));
  return <>
    <ActivityDetail indent={indent}>
      <ActivityKeyValue label={text("Report", "报告")} value={report.body || report.title} />
      <ActivityKeyValue label={text("Kind", "类型")} value={reportKindTag(report.kind, text)} />
      {report.links.map(link => <ActivityNote key={link}><a className={styles.link} href={link} target="_blank" rel="noreferrer">{link}</a></ActivityNote>)}
      {report.deliveries?.map(delivery => <ActivityNote key={`${delivery.channel}:${delivery.account_id}:${delivery.peer_id}`} tone={delivery.status === "failed" ? "err" : undefined}>{deliveryLabel(delivery, text)}</ActivityNote>)}
      {told ? <ActivityNote>{text("It will not report things like this.", "它不会再报告这类事情。")}</ActivityNote> : null}
    </ActivityDetail>
    {told ? null : <ActivityActionRow icon={EyeOff} label={text("Don't report things like this", "不要报告这类事情")} note={text("Every later check-in follows this", "之后每次巡检都会照此执行")} indent={indent}
      disabled={pendingKey !== null && pendingKey !== key} busy={pendingKey === key}
      onClick={() => void context.act(() => addReportPreference(sessionId, dontReportText(report.title)), text("Could not save that.", "未能保存。"), key)} />}
  </>;
}

export function ReportInside({ context, run }: ReportInsideProps) {
  const report = reportOf(run, context);
  return report ? <ReportBody report={report} context={context} /> : <></>;
}

export function AlwaysOnRow({ context, scope }: AlwaysOnRowProps) {
  const { text, sessionId, pendingKey } = context;
  const alwaysOn = context.work?.always_on;
  // Check-ins start from the checked-out branch.
  if (!alwaysOn?.enabled || !scope.active) return null;
  const look = context.alwaysStatus();
  const { config, last_wake: last, today_cost_usd: spent } = alwaysOn;
  const budget = config?.daily_budget_usd ?? null;
  const preferences = alwaysOn.preferences ?? [];
  const wake = () => void context.act(() => wakeConversation(sessionId), text("Could not wake it.", "未能唤醒。"), "wake");
  const held = (pendingKey !== null && pendingKey !== "wake") || alwaysOn.running;
  const lastCost = last?.cost ? wakeCostLabel(last.cost, text) : "";
  const hours = config ? scheduleLabel(config, text) : "";
  const glyph = alwaysOn.agent_id
    ? <span className={styles.avatar}><AgentAvatarById agentId={alwaysOn.agent_id} size={20} /></span>
    : <Glyph name="clock-circle" tone="plan" large />;
  return <ActivityRow id="always-on" glyph={glyph} title={text("Always-on", "常驻")} status={look.label} tone={look.tone}>
    {() => <>
      <ActivityActionRow icon={Play} label={text("Wake now", "立即唤醒")} note={alwaysOn.running ? text("Working now", "正在工作") : text("Looks now; if a turn is running, tries again in 5 minutes", "现在看一次；有回合在运行时 5 分钟后再试")} disabled={held} busy={pendingKey === "wake"} onClick={wake} />
      <ActivityDetail>
        <ActivityNote>{text(
          "This conversation's Agent is set to be always on, so the conversation wakes on its own: it works on the standing goal, or checks in quietly with read-only tools and writes here only when something is worth your attention. Archive the conversation to stop it, which keeps its history and reports; change the schedule in Agents → Always-on.",
          "这个会话的 Agent 设为常驻，所以会话会自己唤醒：推进常驻目标，或在后台用只读工具安静巡检，只有值得你关注时才在这里发消息。归档会话即可停止，历史和报告都会保留；在 Agents → 常驻 里修改计划。",
        )}</ActivityNote>
        {alwaysOn.agent_name ? <ActivityKeyValue label={text("Agent", "代理")} value={alwaysOn.agent_name} /> : null}
        {config?.goal ? <ActivityKeyValue label={text("Standing goal", "常驻目标")} value={config.goal} /> : null}
        {alwaysOn.goal ? <ActivityKeyValue label={text("Goal", "目标")} value={goalStatusLabel(alwaysOn.goal.status, text)} /> : null}
        {config ? (config.wake_minutes != null
          ? <ActivityKeyValue label={text("Wakes every", "唤醒间隔")} value={duration(config.wake_minutes * 60, text)} />
          : <ActivityKeyValue label={text("Wakes", "唤醒")} value={text("When the agent decides, or 1 to 100 hours apart", "由 Agent 决定，否则间隔 1 到 100 小时")} />) : null}
        {hours ? <ActivityKeyValue label={text("Wakes when", "唤醒时间")} value={hours} /> : null}
        {config ? <ActivityKeyValue label={text("Check-in model", "巡检模型")} value={config.check_in_model || text("Same as the conversation", "与会话相同")} /> : null}
        <ActivityKeyValue label={text("Is it alive?", "还在运行吗")} value={aliveLabel(alwaysOn.wake_health, alwaysOn.next_wake_at, alwaysOn.running, Date.now() / 1000, text)} />
        <ActivityKeyValue label={text("Last wake", "上次唤醒")} value={last ? `${outcomeLabel(last.outcome, text)} · ${when(last.at, text)}` : text("Not yet", "还没有")} />
        {lastCost ? <ActivityKeyValue label={text("Its cost", "本次花费")} value={lastCost} /> : null}
        {typeof spent === "number" || budget !== null ? <ActivityKeyValue label={text("Spent today", "今日花费")} value={spendLabel(spent ?? 0, budget, text)} /> : null}
        <ActivityKeyValue label={text("Next wake", "下次唤醒")} value={alwaysOn.next_wake_at
          ? alwaysOn.next_wake_reason ? `${updatedTime(alwaysOn.next_wake_at)} · ${alwaysOn.next_wake_reason}` : updatedTime(alwaysOn.next_wake_at)
          : text("After its next use", "下次使用后")} />
      </ActivityDetail>
      {last?.outcome === "interrupted" ? <ActivityDetail>
        <ActivityKeyValue label={text("The last check-in was interrupted", "上次巡检被中断")} value={last.error || text("It stopped before it finished. Nothing was posted to the conversation.", "它在完成前停止了，会话里没有留下消息。")} />
      </ActivityDetail> : null}
      {alwaysOn.reports.map(report => <ActivityRow key={report.id} id={`always-on:report:${report.id}`} title={report.title} status={report.in_chat === false ? `${text("Not in the chat", "没有进入聊天")} · ${when(report.at, text)}` : when(report.at, text)}>
        {() => <ReportBody report={report} context={context} />}
      </ActivityRow>)}
      <ActivityDetail>
        <ActivityNote>{text("What it was told about reports", "关于报告的要求")}</ActivityNote>
        {preferences.length === 0 ? <ActivityNote>{text("Nothing yet. Tell the agent in the conversation, or use Don't report things like this on a report.", "还没有。可以在会话里告诉 Agent，或在报告上点“不要报告这类事情”。")}</ActivityNote> : null}
      </ActivityDetail>
      {preferences.map(item => {
        const key = `pref:${item.id}`;
        return <ActivityRow key={item.id} id={`always-on:preference:${item.id}`} status={item.text}>
          {() => <ActivityActionRow icon={Trash2} label={text("Delete", "删除")} note={text("Later check-ins stop following this", "之后的巡检不再遵守这一条")}
            disabled={pendingKey !== null && pendingKey !== key} busy={pendingKey === key}
            onClick={() => void context.act(() => removeReportPreference(sessionId, item.id), text("Could not delete it.", "未能删除。"), key)} />}
        </ActivityRow>;
      })}
    </>}
  </ActivityRow>;
}
