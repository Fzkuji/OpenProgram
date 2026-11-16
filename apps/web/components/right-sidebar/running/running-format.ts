/** Labels, tones and times the Running panel shows for executions,
 *  programs and routines. Pure functions of the item. */
import { processIsActive, type ActiveToolCall, type ManagedProcess } from "@/lib/net/execution/process-client";
import { type ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import { executionPending } from "@/lib/execution/activity-model";
import { presentTool } from "@/components/chat/messages/execution/tool-presentation";
import { shortTime } from "../debugger/debugger-presentation";
import { type Routine, type RoutineRun, type TaskRunOutcome, type TaskRunSummary } from "@/lib/chat/conversation-work";
import { seconds, type ProgressLook } from "./program-progress";
import type { Text, Tone } from "./running-rows";

export const LIVE = ["queued", "running", "pausing", "cancelling"];

export function duration(seconds: number, text: Text): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return text(`${s} s`, `${s} 秒`);
  const m = Math.round(s / 60);
  if (m < 60) return text(`${m} min`, `${m} 分钟`);
  const h = Math.floor(m / 60), rest = m % 60;
  return rest ? text(`${h} h ${rest} min`, `${h} 小时 ${rest} 分`) : text(`${h} h`, `${h} 小时`);
}

function dayLabel(seconds: number, text: Text): string {
  const day = new Date(seconds * 1000); day.setHours(0, 0, 0, 0);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - day.getTime()) / 86400000);
  if (diff === 0) return text("Today", "今天");
  if (diff === 1) return text("Yesterday", "昨天");
  return day.toLocaleDateString([], { month: "short", day: "numeric", ...(day.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
}

/** On a row: the time of day for today, the day before that. */
export function when(seconds: number, text: Text): string {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return seconds * 1000 >= today.getTime() ? shortTime(seconds * 1000) : dayLabel(seconds, text);
}

/** What kind of thing a call is, in the reader's words. */
export function callKind(call: ActiveToolCall, text: Text): string {
  const tone = presentTool(call.name, undefined, text).tone;
  if (tone === "agent") return text("Sub-agent", "子代理");
  if (tone === "shell") return text("Command", "命令");
  return call.name;
}

export function executionTone(item: ExecutionSnapshot): Tone {
  if (LIVE.includes(item.status)) return "run";
  if (item.status === "completed") return "ok";
  if (item.status === "failed") return "err";
  return "muted";
}

export function executionState(item: ExecutionSnapshot, text: Text): string {
  if (item.reason_code === "wait_declined") return text("Declined", "已拒绝");
  if (item.status === "paused" && item.reason_code === "wait_open") return text("Waiting for your answer", "等你回答");
  return text(...({
    queued: ["Waiting to start", "等待开始"], running: ["Running", "正在运行"], pausing: ["Pausing", "正在暂停"],
    paused: ["Paused", "已暂停"], cancelling: ["Stopping", "正在停止"],
    reconciliation_required: executionPending(item) ? ["Interrupted", "已中断"] : ["Ended", "已结束"],
    completed: ["Completed", "已完成"], failed: ["Failed", "失败"], cancelled: ["Cancelled", "已取消"], interrupted: ["Interrupted", "已中断"],
  }[item.status] as [string, string] | undefined || ["Status unavailable", "状态不可用"]));
}

export function programState(item: ManagedProcess, text: Text): string {
  if (item.status === "exited" && item.exit_code === 0) return text("Completed", "已完成");
  return text(...({
    starting: ["Starting", "正在启动"], running: ["Running", "正在运行"], stopping: ["Stopping", "正在停止"],
    completed: ["Completed", "已完成"], exited: ["Exited", "已退出"], failed: ["Failed", "失败"],
    stopped: ["Stopped", "已停止"], interrupted: ["Interrupted", "已中断"], unknown: ["Status unknown", "状态未知"], lost: ["Status unknown", "状态未知"],
  }[item.status] as [string, string] | undefined || [item.status, item.status]));
}

/** Going now. A program that lost its supervisor keeps its record open
 *  until it expires, but nothing is known to run: it is history. */
export const programLive = (item: ManagedProcess): boolean =>
  processIsActive(item) && item.status !== "unknown";

export function programTone(item: ManagedProcess): Tone {
  if (programLive(item)) return "run";
  if (["completed", "exited"].includes(item.status)) return "ok";
  if (item.status === "failed") return "err";
  return "muted";
}

/** Ended in an error: it failed, or exited with a code other than 0. */
export const programFailed = (item: ManagedProcess): boolean =>
  item.status === "failed" || (item.status === "exited" && item.exit_code != null && item.exit_code !== 0);

/** Listed in the overview: a program that is going, and one that failed.
 *  What ended normally, was stopped or lost its supervisor is not listed. */
export const programListed = (item: ManagedProcess): boolean =>
  programLive(item) || programFailed(item);

export function programName(item: ManagedProcess, text: Text): string {
  const display = item.display;
  if (display?.kind === "snippet") return `${display.name} ${text("snippet", "代码片段")}`;
  return display?.name || item.command.trim().match(/^(?:[^\s=]+=[^\s]+\s+)*([^\s]+)/)?.[1]?.split("/").pop() || text("Program", "程序");
}

/** What a program does in the model's words, else the program it runs. */
export function programTitle(item: ManagedProcess, text: Text): string {
  return item.title?.trim() || programName(item, text);
}

/** The command on one line, as the row's monospace subtitle. */
export function commandLine(item: ManagedProcess): string {
  return item.command.trim().replace(/\s+/g, " ");
}

/** "eval · 45 / 100 · 3.4/s"; the time left sits at the bar's end. */
function progressLine(look: ProgressLook): string {
  return [look.stage, look.count, look.rate].filter(Boolean).join(" · ");
}

/** A program's status line on its row: progress while it runs, the outcome after. */
export function programStatus(item: ManagedProcess, look: ProgressLook | null, now: number, text: Text): string {
  if (processIsActive(item)) {
    if (item.status === "running" && look && (look.count || look.rate)) return progressLine(look);
    return [`${programState(item, text)} · ${duration(now - item.started_at, text)}`, item.last_line].filter(Boolean).join(" · ");
  }
  if (item.status === "failed" || (item.exit_code != null && item.exit_code !== 0)) {
    const at = look?.count && look.fraction != null && look.fraction < 1 ? text(` at ${look.count}`, `于 ${look.count}`) : "";
    return `${programState(item, text)}${at}${item.exit_code != null ? ` · exit ${item.exit_code}` : ""}`;
  }
  // With a bar, how long it took sits at the bar's end.
  const took = item.ended_at != null ? seconds(item.ended_at - item.started_at, text) : when(item.started_at, text);
  return [programState(item, text), look?.count, look?.fraction == null ? took : null].filter(Boolean).join(" · ");
}

export function programBarTone(item: ManagedProcess, look: ProgressLook | null): "run" | "ok" | "err" | "muted" {
  if (processIsActive(item)) return look?.failed ? "err" : "run";
  if (item.status === "failed" || (item.exit_code != null && item.exit_code !== 0)) return "err";
  return ["completed", "exited"].includes(item.status) ? "ok" : "muted";
}

/** A routine's kind and when it runs, as the Scheduler page names them. */
export function routineRunLabel(run: RoutineRun, text: Text): string {
  const at = new Date(run.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${at} ${text(...({
    quiet: ["Nothing new", "无新内容"], result: ["Sent you a message", "已发消息"], failed: ["Check failed", "检查失败"],
    skipped: ["Skipped: the previous run was still going", "已跳过：上一次运行还没结束"],
    missed: ["Missed: OpenProgram was not running", "错过：当时 OpenProgram 没有运行"],
  }[run.status] as [string, string]))}`;
}

export function routineSchedule(task: Routine, text: Text): string {
  const kind = task.reminder ? text("Reminder", "提醒") : text(...({
    once: ["One-time", "一次性"], recurring: ["Recurring", "周期"], monitor: ["Monitor", "监控"], webhook: ["Webhook", "Webhook"],
  }[task.type] as [string, string]));
  const when = task.type === "webhook" ? text("when called", "被调用时")
    : task.type === "once" ? (task.run_at ? new Date(task.run_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "")
      : task.cron || "";
  const watch = task.watch ? text(" · when its check changes", " · 检查结果变化时") : "";
  return [kind, when].filter(Boolean).join(" · ") + watch + (task.enabled === false ? text(" · Paused", " · 已暂停") : "");
}

const pad2 = (value: number) => String(value).padStart(2, "0");

/** A cron schedule in words, for the shapes tasks are set with; any other
 *  expression is shown as written. */
export function cronWords(cron: string | undefined, text: Text): string {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return cron || "";
  const [minute, hour, day, month, weekday] = parts;
  const everyDay = day === "*" && month === "*";
  const at = /^\d+$/.test(minute) && /^\d+$/.test(hour) ? `${pad2(Number(hour))}:${pad2(Number(minute))}` : "";
  const step = (field: string) => field.match(/^\*\/(\d+)$/)?.[1];
  if (everyDay && weekday === "*") {
    if (minute === "*" && hour === "*") return text("Every minute", "每分钟");
    if (step(minute) && hour === "*") return text(`Every ${step(minute)} minutes`, `每 ${step(minute)} 分钟`);
    if (minute === "0" && hour === "*") return text("Every hour", "每小时");
    if (/^\d+$/.test(minute) && hour === "*") return text(`Every hour at :${pad2(Number(minute))}`, `每小时第 ${Number(minute)} 分`);
    if (/^\d+$/.test(minute) && step(hour)) return text(`Every ${step(hour)} hours`, `每 ${step(hour)} 小时`);
    if (at) return text(`Every day at ${at}`, `每天 ${at}`);
  }
  if (everyDay && weekday === "1-5" && at) return text(`Weekdays at ${at}`, `工作日 ${at}`);
  if (everyDay && /^[0-7]$/.test(weekday) && at) {
    const index = Number(weekday) % 7;
    return text(`${WEEKDAYS_EN[index]}s at ${at}`, `每${WEEKDAYS_ZH[index]} ${at}`);
  }
  if (weekday === "*" && /^\d+$/.test(day) && at) {
    if (month === "*") return text(`Day ${Number(day)} of each month at ${at}`, `每月 ${Number(day)} 日 ${at}`);
    if (/^\d+$/.test(month) && Number(month) >= 1 && Number(month) <= 12) {
      return text(`Every year on ${MONTHS_EN[Number(month) - 1]} ${Number(day)} at ${at}`, `每年 ${Number(month)} 月 ${Number(day)} 日 ${at}`);
    }
  }
  return cron || "";
}

const WEEKDAYS_EN = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const WEEKDAYS_ZH = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A run's outcome as a tone: green when it worked, red when it failed,
 *  yellow when it was stopped, cut off or could not check. */
export function taskOutcomeTone(outcome: TaskRunOutcome): Tone {
  switch (outcome) {
    case "quiet": case "notified": return "ok";
    case "failed": return "err";
    case "running": return "run";
    default: return "warn";
  }
}

export function taskOutcomeWords(outcome: TaskRunOutcome, text: Text): string {
  return text(...({
    running: ["Running", "正在运行"], waiting: ["Waiting for you", "等你处理"], quiet: ["Nothing new", "无新内容"],
    notified: ["Notified", "已通知"], unable: ["Couldn't check", "没能检查"], failed: ["Failed", "失败"],
    stopped: ["Stopped", "已停止"], interrupted: ["Interrupted", "已中断"],
  }[outcome] as [string, string]));
}

/** A time on a task's row: the clock within the last day, else the day. */
export function recentTime(seconds: number, text: Text, now = Date.now() / 1000): string {
  return now - seconds < 86400 ? shortTime(seconds * 1000) : when(seconds, text);
}

/** "178 runs · 9 didn't finish · last 23:00". */
export function taskSummaryLine(summary: TaskRunSummary, text: Text, now?: number): string {
  if (!summary.runs) return text("No runs yet", "还没有运行");
  return [
    text(`${summary.runs} run${summary.runs === 1 ? "" : "s"}`, `${summary.runs} 次运行`),
    summary.unfinished ? text(`${summary.unfinished} didn't finish`, `${summary.unfinished} 次没完成`) : "",
    summary.last_at != null ? text(`last ${recentTime(summary.last_at, text, now)}`, `最近 ${recentTime(summary.last_at, text, now)}`) : "",
  ].filter(Boolean).join(" · ");
}
